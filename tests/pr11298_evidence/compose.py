# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved.
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
root=Path(sys.argv[1]);before=json.loads((root/'before/facts.json').read_text());after=json.loads((root/'after/facts.json').read_text())
assert before['markdown_assistant_sections']==2 and after['markdown_assistant_sections']==1
assert before['csv_assistant_rows']==after['csv_assistant_rows']==2
assert (root/'before/browser.png').read_bytes()!=(root/'after/browser.png').read_bytes()
images=[Image.open(root/side/'browser.png').convert('RGB') for side in ['before','after']]
canvas=Image.new('RGB',(2720,930),'#122033');draw=ImageDraw.Draw(canvas)
try:font=ImageFont.truetype('DejaVuSans.ttf',25)
except OSError:font=ImageFont.load_default(size=25)
for index,(side,sha) in enumerate([('BEFORE','ef00d4946731'),('AFTER','d8616852745a')]):
 draw.text((index*1360+24,15),f'{side} {sha} | selected: Apples | Markdown replies: {2-index}',fill='white',font=font)
 canvas.paste(images[index],(index*1360,70))
canvas.save(root/'before-after.png')
(root/'meta.json').write_text(json.dumps({'pr':11298,'base':'ef00d4946731d2cb9bed2982f1a24346843e7e19','head':'d8616852745a3a63931e8304f622660a0657d5bb','expect':'Selecting Apples exports and uploads one assistant reply after the fix; before also includes rejected Pears. CSV retains both.','limitations':'Real Thread, branch picker and export functions in a Vite browser fixture. History and upload endpoints seeded; backend ingestion not exercised.','before':before,'after':after},indent=2))
