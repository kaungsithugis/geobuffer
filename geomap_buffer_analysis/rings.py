# SPDX-FileCopyrightText: 2026 Kaung Si Thu
# SPDX-License-Identifier: GPL-2.0-or-later

import math
import re

from qgis.PyQt.QtCore import QMetaType
from qgis.core import (
    Qgis,
    QgsFeature,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterString,
    QgsWkbTypes,
)

from .common import BufferAlgorithm, tool_icon


class MultipleRingBuffer(BufferAlgorithm):
    def name(self):
        return "multiple_ring_buffer"

    def displayName(self):
        return "Multiple Ring Buffer"

    def icon(self):
        return tool_icon("rings")

    def shortHelpString(self):
        return (
            "Builds dissolved, non-overlapping planar distance bands around points, lines "
            "or polygons. For example, enter 100, 250, 500 for bands of 0–100, 100–250 "
            "and 250–500 metres. The first band includes source polygon interiors "
            "unless Outside polygons only is enabled. Output attributes store the lower and "
            "upper distance bounds. Use a projected analysis CRS in metres. When left "
            "blank, the tool prefers a suitable input CRS and otherwise uses the project CRS."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "INPUT", "Input features", [Qgis.ProcessingSourceType.VectorAnyGeometry]
        ))
        self.add_analysis_crs()
        self.addParameter(QgsProcessingParameterString(
            "DISTANCES", "Buffer distances in metres (comma separated)", "100, 250, 500"
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            "OUTSIDE", "Outside polygons only", defaultValue=False
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            "OUTPUT", "Buffer rings", Qgis.ProcessingSourceType.VectorPolygon
        ))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, "INPUT", context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, "INPUT"))
        crs = self.analysis_crs(parameters, source.sourceCrs(), context)

        distance_text = self.parameterAsString(parameters, "DISTANCES", context).strip()
        try:
            distances = sorted({float(item) for item in re.split(r"[,;\s]+", distance_text)})
        except ValueError as error:
            raise QgsProcessingException("Enter numeric distances, for example 100, 250, 500.") from error
        if not distances or any(not math.isfinite(d) or d <= 0 for d in distances):
            raise QgsProcessingException("All ring distances must be finite and greater than zero.")

        outside = self.parameterAsBool(parameters, "OUTSIDE", context)
        if outside and QgsWkbTypes.geometryType(source.wkbType()) != Qgis.GeometryType.Polygon:
            raise QgsProcessingException("Outside polygons only requires a polygon input layer.")

        request = QgsFeatureRequest().setNoAttributes()
        request.setDestinationCrs(crs, context.transformContext())
        request.setInvalidGeometryCheck(Qgis.InvalidGeometryCheck.NoCheck)
        geometries = []
        for feature in source.getFeatures(request):
            if feedback.isCanceled():
                return {}
            geometry = feature.geometry()
            if geometry.isEmpty():
                continue
            if geometry.type() == Qgis.GeometryType.Polygon and not geometry.isGeosValid():
                raise QgsProcessingException(
                    f"Feature {feature.id()} has invalid polygon geometry. Run Fix Geometries first."
                )
            geometries.append(geometry)

        if not geometries:
            raise QgsProcessingException("The input contains no usable features.")

        previous = self.dissolve(geometries) if outside else None
        fields = QgsFields()
        fields.append(QgsField("from_m", QMetaType.Type.Double))
        fields.append(QgsField("to_m", QMetaType.Type.Double))
        rings = []
        lower = 0.0
        for index, upper in enumerate(distances):
            feedback.setProgressText(f"Building the {upper:g} m buffer")
            buffers = []
            for geometry in geometries:
                if feedback.isCanceled():
                    return {}
                buffer = geometry.buffer(upper, 24)
                if buffer.isEmpty() or buffer.lastError():
                    raise QgsProcessingException(
                        f"Buffer generation failed at {upper:g} m. Check input geometry and distances."
                    )
                buffers.append(buffer)

            outer = self.dissolve(buffers)
            ring = outer if previous is None else outer.difference(previous)
            if ring.lastError():
                raise QgsProcessingException(f"Could not build the {lower:g}–{upper:g} m ring: {ring.lastError()}")
            if not ring.isEmpty():
                if not ring.convertToMultiType() or ring.wkbType() != Qgis.WkbType.MultiPolygon:
                    raise QgsProcessingException("A ring could not be converted to a multipolygon.")
                feature = QgsFeature(fields)
                feature.setGeometry(ring)
                feature.setAttributes([lower, upper])
                rings.append(feature)
            previous = outer
            lower = upper
            feedback.setProgress(95 * (index + 1) / len(distances))

        if feedback.isCanceled():
            return {}
        if not rings:
            raise QgsProcessingException("The selected distances did not produce any polygon rings.")
        sink, destination = self.parameterAsSink(
            parameters, "OUTPUT", context, fields, Qgis.WkbType.MultiPolygon, crs
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, "OUTPUT"))
        if not sink.addFeatures(rings, QgsFeatureSink.Flag.FastInsert):
            raise QgsProcessingException(f"Could not write the ring layer: {sink.lastError()}")
        if not sink.flushBuffer():
            raise QgsProcessingException(f"Could not finish writing the ring layer: {sink.lastError()}")
        del sink
        feedback.setProgress(100)
        return {"OUTPUT": destination}

    @staticmethod
    def dissolve(geometries):
        dissolved = QgsGeometry.unaryUnion(geometries)
        if dissolved.isEmpty() or dissolved.lastError():
            raise QgsProcessingException(f"Could not dissolve the buffers: {dissolved.lastError()}")
        return dissolved
