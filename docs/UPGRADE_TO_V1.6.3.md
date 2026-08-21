# Upgrade to v1.6.3

v1.6.3 restores real vector/text table reconstruction as the default.

## Key change

The source table raster is used only as a measurement reference. It is **not** inserted as the visible table.

The reconstructed table uses:

- real PDF vector rules;
- real visible/selectable/searchable PDF text;
- source-derived row/column geometry;
- robust table-wide body/header font-size estimation;
- source-derived line position, alignment and colour;
- conservative horizontal scaling only (no vertical font stretching).

This replaces the v1.6.2 hybrid crop strategy and avoids the per-cell font-size instability from v1.6.0.
