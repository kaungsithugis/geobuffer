# GeoBuffer 0.2.2

A QGIS Processing plugin by Kaung Si Thu. This experimental
release targets QGIS 3.40–3.x and was tested with QGIS 3.40.6 on Linux.

## Install and open

1. Open QGIS and go to **Plugins → Manage and Install Plugins → Install from ZIP**.
2. Select `GeoBuffer_v0.2.2.zip` and click **Install Plugin**.
3. Open **Processing → Toolbox** and search for **Euclidean Distance**,
   **Multiple Ring Buffer**, or **Reclassify Distance**. The three tools
   also appear under **Plugins → GeoBuffer**.

Use a **projected analysis CRS with metre units** that fits the study area.
For Myanmar, common choices are WGS 84 / UTM zone 46N (EPSG:32646) for
90°–96°E and zone 47N (EPSG:32647) for 96°–102°E. For study areas spanning
zones, choose a suitable projection for the entire area. When Analysis CRS
is blank, the plugin uses the input CRS if suitable and otherwise tries
the project CRS. The input layer must have its correct source CRS assigned.

## Euclidean Distance

Creates a Float32 GeoTIFF with the planar straight-line distance in metres
from each output cell centre to the nearest source cell centre.

- **Input** can be a point, line, or polygon layer, or an affine georeferenced
  raster readable by GDAL. Vector features are rasterized on the output grid.
  A smaller cell size helps retain small polygons or narrow features.
- For a raster, the tool uses band 1 and recognizes file NoData and validity
  masks. By default, zero is a valid source value. Choose **Nonzero valid
  cells** for a binary raster where 0 is background and 1 is a source. The
  source mask is aligned to the analysis grid with nearest-neighbour
  resampling.
- **Output cell size** defaults to 30 m. **Maximum distance** defaults to
  0 (unlimited). When a positive maximum is set, values above it become NoData.
- **Reference raster grid** copies the origin, CRS, and pixel dimensions of a
  north-up raster in a projected CRS with metre units. Its dimensions are used
  by default. A processing extent can select a smaller aligned window and will
  be snapped outward to full cells on the reference grid. Cell Size is ignored
  when a reference raster is selected. The reference raster supplies the grid
  and its pixel values are unused.
- **Output mask** accepts an AOI polygon layer. Cells with centres outside
  the AOI become NoData after calculation, so sources outside the polygon
  can still contribute if they are on the processing grid. The mask does not
  change the grid's extent. Use a processing extent with some room around the
  AOI to include nearby sources and to keep large jobs within the 12-million-
  cell limit.
- Without a reference raster or explicit extent, the tool pads input bounds
  by 10 cells, or by the maximum distance plus one cell when a limit is set.
- Choose a new `.tif` or `.tiff` file, or use the temporary output option.

**Example.** For distance to a fault layer, select the fault lines, use a
30 m cell size, set the processing extent to cover the area of interest, and
enter 5000 m as the maximum distance.

The distance tool uses SciPy in QGIS's Python environment. To check whether it
is available, open **Plugins → Python Console** and run `import scipy`.
On Windows, an OSGeo4W-managed QGIS installation can add the
`python3-scipy` package through the OSGeo4W installer. On Debian or Ubuntu
with system Python, the package is also named `python3-scipy`. Restart QGIS
after installing it. Multiple Ring Buffer runs independently of SciPy.

## Reclassify Distance

Assigns integer scores to distance bands and writes a classified GeoTIFF
with the same grid and CRS as the input. Enter ascending positive cutoffs
in metres and one more score than cutoff. Values exactly at a cutoff belong
to the lower distance band.

| Cutoffs | Scores | Result |
|---|---|---|
| `1000, 3000, 5000` | `4, 3, 2, 1` | 0–1000 m gets 4, >1000–3000 m gets 3, >3000–5000 m gets 2, >5000 m gets 1 |

Input NoData, masked pixels, and invalid distances remain NoData in the
classified raster. The default scores are examples. Choose cutoffs and
scores that fit the criteria of your analysis.

## Multiple Ring Buffer

Creates dissolved, non-overlapping polygon bands around all input points,
lines, or polygons. Enter distances in metres. For example, `100, 250, 500`
creates bands of 0–100 m, 100–250 m, and 250–500 m. Each band is measured from
the nearest input feature. Distances are sorted and duplicates are removed.
The output fields `from_m` and `to_m` store each band's distance bounds.

For polygon input, the first band includes the source polygon interiors by
default. Enable **Outside polygons only** to exclude them. Save the result
to GeoPackage or open it as a temporary layer. To buffer only selected
features, enable **Selected features only** next to the input layer.

These are planar tools. Source rasterization, grid alignment, and projection
settings affect comparisons with results from other GIS software.

## License

Copyright © 2026 Kaung Si Thu. Distributed under the GNU General Public
License, version 2 or any later version. See [LICENSE](LICENSE).
