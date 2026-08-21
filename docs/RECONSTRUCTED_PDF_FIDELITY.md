# Precision Reconstructed PDF geometry

Version 1.3.0 changes Reconstructed PDF from block-fit layout to a source-pixel-guided reconstruction.

## Geometry pipeline

1. Unlimited-OCR provides the recognized text and a normalized 0..999 region bbox.
2. The app maps that bbox to the exact 200-DPI page raster that was sent to the model.
3. Horizontal ink projections detect the real printed text rows inside the model region.
4. OCR wording is allocated across those physical rows.
5. A TrueType font is chosen (regular or heading-bold when available).
6. Pillow/FreeType measures the text-specific glyph ink bounds relative to its baseline.
7. Font size, baseline and horizontal morph are calculated so the visible PDF glyph ink fits the detected source ink rectangle.
8. Word-level x-positioning is used only when all detected word boxes can be reproduced without excessive horizontal distortion; otherwise the whole line is fitted as one natural-looking vector line.

## Images and structured content

Image/figure/chart regions are cropped from the exact source raster and inserted at the mapped PDF coordinates. Table and equation regions are tightened to their visible source-content bounds before the structured renderer is invoked.

## Why the result cannot be mathematically identical in every scan

OCR output does not contain the original font file, original font metrics, kerning pairs, paragraph styles or source application layout metadata. The builder therefore matches geometry first and uses locally available fonts as visual substitutes. Position and apparent text size can be matched very closely, but exact glyph outlines depend on whether the substitute font resembles the original.

## Fallbacks

If physical line segmentation is not credible, the previous block-fit visible-text renderer is used. If structured rendering fails, the original detected region is inserted as a raster crop so content is not lost.
