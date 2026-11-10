# SPDX-FileCopyrightText: 2026 Kaung Si Thu
# SPDX-License-Identifier: GPL-2.0-or-later

from pathlib import Path

from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterCrs,
)


def tool_icon(name):
    return QIcon(str(Path(__file__).with_name("icons") / f"{name}.svg"))


class BufferAlgorithm(QgsProcessingAlgorithm):
    def group(self):
        return "Distance and buffers"

    def groupId(self):
        return "distanceandbuffers"

    def createInstance(self):
        return type(self)()

    def add_analysis_crs(self):
        self.addParameter(QgsProcessingParameterCrs(
            "CRS", "Analysis CRS (projected, metres)", optional=True
        ))

    def analysis_crs(self, parameters, source_crs, context):
        if not source_crs.isValid():
            raise QgsProcessingException("Assign the correct CRS to the input layer first.")

        crs = self.parameterAsCrs(parameters, "CRS", context)
        if parameters.get("CRS") in (None, ""):
            if self.is_metric(source_crs):
                crs = source_crs
            elif context.project() is not None:
                crs = context.project().crs()
        if not self.is_metric(crs):
            raise QgsProcessingException(
                "Choose a projected analysis CRS in metres, such as the WGS 84 / UTM zone "
                "that covers your study area."
            )
        return crs

    @staticmethod
    def is_metric(crs):
        return (
            crs.isValid()
            and crs.type() == Qgis.CrsType.Projected
            and crs.mapUnits() == Qgis.DistanceUnit.Meters
        )


def coordinate_transform(source_crs, analysis_crs, context):
    return QgsCoordinateTransform(source_crs, analysis_crs, context.transformContext())
