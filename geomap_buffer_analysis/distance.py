# SPDX-FileCopyrightText: 2026 Kaung Si Thu
# SPDX-License-Identifier: GPL-2.0-or-later

import math
import os
from tempfile import TemporaryDirectory

import numpy as np
from osgeo import gdal, ogr
from qgis.core import (
    Qgis,
    QgsCsException,
    QgsFeatureRequest,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterMapLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
)

from .common import BufferAlgorithm, coordinate_transform, tool_icon
from .raster_io import check_output, open_raster, publish_output, temporary_output


class EuclideanDistance(BufferAlgorithm):
    MAX_CELLS = 12_000_000
    NODATA = -9999.0

    def name(self):
        return "euclidean_distance"

    def displayName(self):
        return "Euclidean Distance"

    def icon(self):
        return tool_icon("distance")

    def shortHelpString(self):
        return (
            "Creates a Float32 GeoTIFF with the straight-line distance in metres from each "
            "grid cell centre to the nearest source cell centre. Use points, lines, polygons "
            "or a GDAL-readable raster. For rasters, band 1 is used and valid zero cells "
            "count as sources by default. Select Nonzero valid cells for a 0/1 source mask. "
            "A reference raster supplies the projected CRS in metres, pixel dimensions and grid origin. "
            "Use an optional processing extent to calculate on a smaller aligned grid. "
            "An AOI polygon masks the output after calculation, so sources outside the polygon "
            "but inside the processing grid still contribute. Zero maximum distance means unlimited. "
            "Without a reference raster or explicit extent, the input bounds are padded by the "
            "maximum distance plus one cell, or by 10 cells if the distance is unlimited."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMapLayer(
            "INPUT", "Input features or source raster",
            types=[QgsProcessing.TypeVectorAnyGeometry, QgsProcessing.TypeRaster]
        ))
        self.add_analysis_crs()
        self.addParameter(QgsProcessingParameterRasterLayer(
            "REFERENCE", "Reference raster grid (optional, sets CRS and cell size)", optional=True
        ))
        for name, label, default, minimum in (
            ("CELL_SIZE", "Output cell size (m)", 30.0, 0.001),
            ("MAX_DISTANCE", "Maximum distance (m, 0 for unlimited)", 0.0, 0.0),
        ):
            parameter = QgsProcessingParameterNumber(
                name, label, QgsProcessingParameterNumber.Type.Double, default, minValue=minimum
            )
            parameter.setMetadata({"widget_wrapper": {"decimals": 3}})
            self.addParameter(parameter)
        self.addParameter(QgsProcessingParameterExtent(
            "EXTENT", "Processing extent (optional)", optional=True
        ))
        self.addParameter(QgsProcessingParameterFeatureSource(
            "AOI", "Output mask (optional polygon layer)",
            [QgsProcessing.TypeVectorPolygon], optional=True
        ))
        self.addParameter(QgsProcessingParameterEnum(
            "RASTER_MODE", "Raster sources (band 1 only)",
            options=["All valid cells (including zero)", "Nonzero valid cells"], defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterRasterDestination(
            "OUTPUT", "Distance raster (GeoTIFF)"
        ))

    def prepareAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsLayer(parameters, "INPUT", context)
        if not isinstance(layer, (QgsRasterLayer, QgsVectorLayer)) or not layer.isValid():
            raise QgsProcessingException("Select a valid input feature layer or raster.")
        if not layer.crs().isValid():
            raise QgsProcessingException("Assign the correct CRS to the input layer first.")
        self.maximum = self.parameterAsDouble(parameters, "MAX_DISTANCE", context)
        if not math.isfinite(self.maximum) or self.maximum < 0:
            raise QgsProcessingException("Use a finite, nonnegative maximum distance.")
        reference = self.parameterAsRasterLayer(parameters, "REFERENCE", context)
        if reference is not None:
            self.crs = reference.crs()
            if not self.is_metric(self.crs):
                raise QgsProcessingException("The reference raster must use a projected CRS in metres.")
            if (
                parameters.get("CRS") not in (None, "")
                and self.parameterAsCrs(parameters, "CRS", context) != self.crs
            ):
                raise QgsProcessingException("Clear Analysis CRS or set it to the reference raster's CRS.")
            self.transform, self.width, self.height = self.reference_grid(reference, parameters, context)
        else:
            self.crs = self.analysis_crs(parameters, layer.crs(), context)
            size = self.parameterAsDouble(parameters, "CELL_SIZE", context)
            if not math.isfinite(size) or size < 0.001:
                raise QgsProcessingException("Use a cell size of at least 0.001 m.")
            extent = self.grid_extent(parameters, layer, self.crs, context, size, self.maximum)
            self.transform = (extent.xMinimum(), size, 0, extent.yMaximum(), 0, -size)
            self.width = max(1, math.ceil(extent.width() / size))
            self.height = max(1, math.ceil(extent.height() / size))
        self.vector_source = QgsVectorLayerFeatureSource(layer) if isinstance(layer, QgsVectorLayer) else None
        self.raster_path = layer.source() if isinstance(layer, QgsRasterLayer) else None
        self.source_crs = layer.crs()
        return True

    def processAlgorithm(self, parameters, context, feedback):
        try:
            from scipy.ndimage import distance_transform_edt
        except ImportError as error:
            raise QgsProcessingException(
                "Euclidean Distance needs SciPy in QGIS's Python environment. "
                "See the plugin README for installation instructions."
            ) from error

        output = self.parameterAsOutputLayer(parameters, "OUTPUT", context)
        check_output(output)
        nx, ny = self.width, self.height
        if nx * ny > self.MAX_CELLS:
            raise QgsProcessingException(
                f"The grid is {nx:,} × {ny:,} cells and exceeds the {self.MAX_CELLS:,}-cell limit. "
                "Use a smaller processing extent, larger cell size, or coarser reference raster."
            )

        grid = gdal.GetDriverByName("MEM").Create("", nx, ny, 1, gdal.GDT_Byte)
        if grid is None:
            raise QgsProcessingException("Could not allocate the raster grid.")
        grid.SetGeoTransform(self.transform)
        grid.SetProjection(self.crs.toWkt())
        grid.GetRasterBand(1).Fill(0)
        feedback.setProgressText(f"Preparing sources for a {nx:,} × {ny:,} cell grid")
        if self.raster_path is not None:
            self.raster_sources(grid, parameters, context, feedback)
        else:
            self.rasterize_features(self.vector_source, grid, context, feedback)
        if feedback.isCanceled():
            return {}

        sources = grid.ReadAsArray()
        if sources is None or not sources.any():
            raise QgsProcessingException(
                "No source cells were found on the grid. Check the extent, cell size and source data."
            )

        feedback.setProgress(35)
        feedback.setProgressText("Calculating straight-line distances")
        distances = distance_transform_edt(sources == 0, sampling=(-self.transform[5], self.transform[1]))
        if feedback.isCanceled():
            return {}
        if self.maximum > 0:
            distances[distances > self.maximum] = self.NODATA

        if parameters.get("AOI") not in (None, ""):
            aoi = self.parameterAsSource(parameters, "AOI", context)
            if aoi is None or not aoi.sourceCrs().isValid():
                raise QgsProcessingException("Select a polygon mask with a valid source CRS.")
            feedback.setProgressText("Masking the distance raster")
            grid.GetRasterBand(1).Fill(0)
            self.rasterize_features(aoi, grid, context, feedback)
            if feedback.isCanceled():
                return {}
            mask = grid.ReadAsArray()
            if mask is None or not mask.any():
                raise QgsProcessingException("No cell centres fall inside the polygon mask on the processing grid.")
            distances[mask == 0] = self.NODATA

        feedback.setProgress(85)
        feedback.setProgressText("Writing the GeoTIFF")
        if not self.write_raster(output, grid, distances, feedback):
            return {}
        feedback.setProgress(100)
        return {"OUTPUT": output}

    def reference_grid(self, reference, parameters, context):
        raster = open_raster(reference.source(), "Reference raster")
        transform = raster.GetGeoTransform()
        if not all(map(math.isfinite, transform)) or (
            transform[2] != 0 or transform[4] != 0 or transform[1] <= 0 or transform[5] >= 0
        ):
            raise QgsProcessingException("Use a north-up reference raster without rotation or shear.")
        if parameters.get("EXTENT") in (None, ""):
            return transform, raster.RasterXSize, raster.RasterYSize

        extent = self.parameterAsExtent(parameters, "EXTENT", context, self.crs)
        if extent.isEmpty() or not extent.isFinite():
            raise QgsProcessingException("Choose a finite extent with a positive width and height.")
        x, dx, _, y, _, dy = transform
        # Round away sub-pixel arithmetic noise before snapping outward.
        left = math.floor(round((extent.xMinimum() - x) / dx, 9))
        right = math.ceil(round((extent.xMaximum() - x) / dx, 9))
        top = math.floor(round((extent.yMaximum() - y) / dy, 9))
        bottom = math.ceil(round((extent.yMinimum() - y) / dy, 9))
        return (x + left * dx, dx, 0, y + top * dy, 0, dy), max(1, right - left), max(1, bottom - top)

    def grid_extent(self, parameters, layer, crs, context, size, maximum):
        if parameters.get("EXTENT") not in (None, ""):
            extent = self.parameterAsExtent(parameters, "EXTENT", context, crs)
            if extent.isEmpty():
                raise QgsProcessingException("Choose an extent with a positive width and height.")
        else:
            transform = coordinate_transform(layer.crs(), crs, context)
            try:
                extent = transform.transformBoundingBox(layer.extent())
            except QgsCsException as error:
                raise QgsProcessingException(f"Could not transform the input extent: {error}") from error
            # One extra cell includes features exactly on the source bounds.
            extent.grow(maximum + size if maximum else 10 * size)
        if not extent.isFinite() or extent.isEmpty():
            raise QgsProcessingException("The processing extent is invalid in the analysis CRS.")
        return extent

    def rasterize_features(self, source, grid, context, feedback):
        memory = ogr.GetDriverByName("Memory").CreateDataSource("")
        sources = memory.CreateLayer("sources", grid.GetSpatialRef())
        request = QgsFeatureRequest().setNoAttributes()
        request.setDestinationCrs(self.crs, context.transformContext())
        request.setInvalidGeometryCheck(Qgis.InvalidGeometryCheck.NoCheck)
        for feature in source.getFeatures(request):
            if feedback.isCanceled():
                return
            geometry = feature.geometry()
            if geometry.isEmpty():
                continue
            if geometry.type() == Qgis.GeometryType.Polygon and not geometry.isGeosValid():
                raise QgsProcessingException(
                    f"Feature {feature.id()} has invalid polygon geometry. Run Fix Geometries first."
                )
            geometry.convertToStraightSegment()
            ogr_geometry = ogr.CreateGeometryFromWkb(bytes(geometry.asWkb()))
            if ogr_geometry is None:
                raise QgsProcessingException(f"Could not read geometry for feature {feature.id()}.")
            target = ogr.Feature(sources.GetLayerDefn())
            target.SetGeometry(ogr_geometry)
            if sources.CreateFeature(target) != ogr.OGRERR_NONE:
                raise QgsProcessingException(f"Could not prepare feature {feature.id()} for rasterization.")
        status = gdal.RasterizeLayer(grid, [1], sources, burn_values=[1])
        if status != gdal.CE_None:
            raise QgsProcessingException("Could not rasterize the input features or polygon mask.")

    def raster_sources(self, grid, parameters, context, feedback):
        source = open_raster(self.raster_path, "Source raster")
        width, height = source.RasterXSize, source.RasterYSize
        band = source.GetRasterBand(1)
        validity = band.GetMaskBand()
        nonzero = self.parameterAsEnum(parameters, "RASTER_MODE", context) == 1
        # A disk-backed mask keeps memory bounded even when the input raster is large.
        with TemporaryDirectory(prefix="geomap-source-") as temp:
            mask = gdal.GetDriverByName("GTiff").Create(
                os.path.join(temp, "mask.tif"), width, height, 1, gdal.GDT_Byte,
                options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
            )
            if mask is None:
                raise QgsProcessingException("Could not create a temporary source raster mask.")
            try:
                mask.SetGeoTransform(source.GetGeoTransform())
                mask.SetProjection(self.source_crs.toWkt())
                mask_band = mask.GetRasterBand(1)
                rows = max(1, min(256, 1_000_000 // width))
                for start in range(0, height, rows):
                    if feedback.isCanceled():
                        return
                    count = min(rows, height - start)
                    values = band.ReadAsArray(0, start, width, count)
                    valid = validity.ReadAsArray(0, start, width, count)
                    if values is None or valid is None:
                        raise QgsProcessingException("Could not read the input raster or its validity mask.")
                    cells = (valid != 0) & np.isfinite(values)
                    if nonzero:
                        cells &= values != 0
                    if mask_band.WriteArray(cells.astype(np.uint8), 0, start) != gdal.CE_None:
                        raise QgsProcessingException("Could not write the temporary raster mask.")

                status = gdal.ReprojectImage(mask, grid, None, None, gdal.GRA_NearestNeighbour)
                if status != gdal.CE_None:
                    raise QgsProcessingException("Could not align the source raster to the analysis grid.")
            finally:
                mask = None

    def write_raster(self, output, grid, distances, feedback):
        with temporary_output(output) as temporary:
            raster = gdal.GetDriverByName("GTiff").Create(
                temporary, grid.RasterXSize, grid.RasterYSize, 1, gdal.GDT_Float32,
                options=["TILED=YES", "COMPRESS=LZW", "PREDICTOR=3"],
            )
            if raster is None:
                raise QgsProcessingException("Could not create the output GeoTIFF.")
            raster.SetGeoTransform(grid.GetGeoTransform())
            raster.SetProjection(grid.GetProjection())
            band = raster.GetRasterBand(1)
            band.SetNoDataValue(self.NODATA)
            band.SetUnitType("m")
            status = band.WriteArray(distances.astype(np.float32))
            band = None
            flush_status = raster.FlushCache()
            raster = None
            if status != gdal.CE_None or flush_status not in (None, gdal.CE_None):
                raise QgsProcessingException("Could not write all GeoTIFF cells.")
            if feedback.isCanceled():
                return False
            publish_output(temporary, output)
        return True
