# SPDX-FileCopyrightText: 2026 Kaung Si Thu
# SPDX-License-Identifier: GPL-2.0-or-later

import math

import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsProcessingException,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)

from .common import BufferAlgorithm, tool_icon
from .raster_io import check_output, open_raster, publish_output, temporary_output


class ReclassifyDistance(BufferAlgorithm):
    NODATA = -2_147_483_648
    MAX_SCORE = 2_147_483_647

    def name(self):
        return "reclassify_distance"

    def displayName(self):
        return "Reclassify Distance"

    def icon(self):
        return tool_icon("reclassify")

    def shortHelpString(self):
        return (
            "Assign integer scores to nonnegative distances in band 1 of a raster in metres. "
            "Enter ascending comma-separated cutoffs and one more score than cutoff. "
            "With cutoffs 1000, 3000, 5000 and scores 4, 3, 2, 1, the bands are "
            "0–1000, >1000–3000, >3000–5000 and >5000 m. Values at each cutoff belong "
            "to the lower distance band. The output retains the grid and CRS. "
            "Input NoData, masked, negative, and nonfinite cells become output NoData."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer("INPUT", "Distance raster (band 1, metres)"))
        self.addParameter(QgsProcessingParameterString(
            "CUTOFFS", "Ascending distance cutoffs (m, comma separated)", "1000, 3000, 5000"
        ))
        self.addParameter(QgsProcessingParameterString(
            "SCORES", "Integer scores (nearest to farthest, comma separated)", "4, 3, 2, 1"
        ))
        self.addParameter(QgsProcessingParameterRasterDestination("OUTPUT", "Classified raster (GeoTIFF)"))

    def prepareAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, "INPUT", context)
        if layer is None or not layer.isValid() or not layer.crs().isValid():
            raise QgsProcessingException("Select a distance raster with a valid CRS.")
        self.source_path = layer.source()
        self.projection = layer.crs().toWkt()
        return True

    def processAlgorithm(self, parameters, context, feedback):
        cutoffs, scores = self.classes(parameters, context)
        source = open_raster(self.source_path, "Distance raster")
        output = self.parameterAsOutputLayer(parameters, "OUTPUT", context)
        check_output(output)

        with temporary_output(output) as temporary:
            if not self.write_classes(source, temporary, cutoffs, scores, feedback) or feedback.isCanceled():
                return {}
            publish_output(temporary, output)
        feedback.setProgress(100)
        return {"OUTPUT": output}

    def classes(self, parameters, context):
        try:
            cutoffs = [float(value) for value in self.parameterAsString(parameters, "CUTOFFS", context).split(",")]
            scores = [int(value) for value in self.parameterAsString(parameters, "SCORES", context).split(",")]
        except ValueError as error:
            raise QgsProcessingException("Enter comma-separated numeric cutoffs and integer scores.") from error
        if any(not math.isfinite(value) or value <= 0 for value in cutoffs) or any(
            first >= second for first, second in zip(cutoffs, cutoffs[1:])
        ):
            raise QgsProcessingException("Cutoffs must be finite, positive, and strictly increasing.")
        if len(scores) != len(cutoffs) + 1:
            raise QgsProcessingException("Enter one more score than cutoff, including a score above the last cutoff.")
        if any(not self.NODATA < score <= self.MAX_SCORE for score in scores):
            raise QgsProcessingException("Scores must be between -2147483647 and 2147483647.")
        return np.array(cutoffs, dtype=np.float64), np.array(scores, dtype=np.int32)

    def write_classes(self, source, path, cutoffs, scores, feedback):
        width, height = source.RasterXSize, source.RasterYSize
        raster = gdal.GetDriverByName("GTiff").Create(
            path, width, height, 1, gdal.GDT_Int32,
            options=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
        )
        if raster is None:
            raise QgsProcessingException("Could not create the classified GeoTIFF.")
        raster.SetGeoTransform(source.GetGeoTransform())
        raster.SetProjection(self.projection)
        target = raster.GetRasterBand(1)
        target.SetNoDataValue(self.NODATA)
        target.SetDescription("Distance class score")

        band = source.GetRasterBand(1)
        if gdal.DataTypeIsComplex(band.DataType):
            raise QgsProcessingException("The distance raster must contain real numbers.")
        mask_band = band.GetMaskBand()
        rows = max(1, min(256, 1_000_000 // width))
        feedback.setProgressText("Classifying distance cells")
        for row in range(0, height, rows):
            if feedback.isCanceled():
                return False
            count = min(rows, height - row)
            distances = band.ReadAsArray(0, row, width, count)
            mask = mask_band.ReadAsArray(0, row, width, count)
            if distances is None or mask is None:
                raise QgsProcessingException("Could not read the input distance cells.")
            valid = (mask != 0) & np.isfinite(distances) & (distances >= 0)
            classified = np.full(distances.shape, self.NODATA, dtype=np.int32)
            classified[valid] = scores[np.searchsorted(cutoffs, distances[valid], side="left")]
            if target.WriteArray(classified, 0, row) != gdal.CE_None:
                raise QgsProcessingException("Could not write the classified distance cells.")
            feedback.setProgress(99 * (row + count) / height)

        target = None
        flush_status = raster.FlushCache()
        raster = None
        if flush_status not in (None, gdal.CE_None):
            raise QgsProcessingException("Could not finish writing the classified raster.")
        return True
