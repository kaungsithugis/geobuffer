# SPDX-FileCopyrightText: 2026 Kaung Si Thu
# SPDX-License-Identifier: GPL-2.0-or-later

import processing
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication, QgsProcessingProvider

from .common import tool_icon
from .distance import EuclideanDistance
from .reclassify import ReclassifyDistance
from .rings import MultipleRingBuffer


class BufferProvider(QgsProcessingProvider):
    def id(self):
        return "geomapbuffer"

    def name(self):
        return "GeoBuffer"

    def icon(self):
        return tool_icon("rings")

    def loadAlgorithms(self):
        self.addAlgorithm(EuclideanDistance())
        self.addAlgorithm(MultipleRingBuffer())
        self.addAlgorithm(ReclassifyDistance())


class BufferAnalysisPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = BufferProvider()
        self.actions = []

    def initGui(self):
        QgsApplication.processingRegistry().addProvider(self.provider)
        for algorithm in self.provider.algorithms():
            action = QAction(algorithm.icon(), algorithm.displayName(), self.iface.mainWindow())
            algorithm_id = algorithm.id()
            action.triggered.connect(
                lambda checked=False, alg_id=algorithm_id: processing.execAlgorithmDialog(alg_id)
            )
            self.iface.addPluginToMenu(self.provider.name(), action)
            self.actions.append(action)

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.provider.name(), action)
            action.deleteLater()
        self.actions.clear()
        QgsApplication.processingRegistry().removeProvider(self.provider)
