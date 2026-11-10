# SPDX-FileCopyrightText: 2026 Kaung Si Thu
# SPDX-License-Identifier: GPL-2.0-or-later

import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from osgeo import gdal
from qgis.core import QgsProcessingException


def open_raster(path, label):
    try:
        raster = gdal.Open(path, gdal.GA_ReadOnly)
    except RuntimeError:
        raster = None
    if raster is None or not raster.RasterCount or raster.GetGeoTransform(can_return_null=True) is None:
        raise QgsProcessingException(
            f"{label} must be a GDAL-readable raster with an affine geotransform, such as a GeoTIFF."
        )
    return raster


def check_output(path):
    if Path(path).suffix.lower() not in (".tif", ".tiff"):
        raise QgsProcessingException("Save the output as a GeoTIFF (.tif or .tiff).")
    if Path(path).exists():
        raise QgsProcessingException("Choose a new filename for the output GeoTIFF.")


@contextmanager
def temporary_output(path):
    with TemporaryDirectory(prefix=".geobuffer-", dir=str(Path(path).parent)) as folder:
        yield os.path.join(folder, "output.tif")


def publish_output(temporary, output):
    check_output(output)
    os.replace(temporary, output)
