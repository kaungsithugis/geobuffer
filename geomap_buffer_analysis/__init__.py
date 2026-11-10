# SPDX-FileCopyrightText: 2026 Kaung Si Thu
# SPDX-License-Identifier: GPL-2.0-or-later

def classFactory(iface):
    from .plugin import BufferAnalysisPlugin

    return BufferAnalysisPlugin(iface)
