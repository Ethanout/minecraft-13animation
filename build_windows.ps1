$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python -m PyInstaller --noconfirm --clean --onedir --name minecraft-13animation --paths tools --hidden-import PIL.Image --hidden-import PIL.ImageOps --hidden-import tkinter.scrolledtext --add-data 'examples/blocksequencer_mapping/objmc_fixed;examples/blocksequencer_mapping/objmc_fixed' --add-data 'examples/blocksequencer_mapping/example_sources/thirdperson.png;examples/blocksequencer_mapping/example_sources' --add-data 'examples/blocksequencer_mapping/example_sources/thirdperson;examples/blocksequencer_mapping/example_sources/thirdperson' tools/desktop_entry.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
Copy-Item -LiteralPath README.md -Destination dist/minecraft-13animation/README.md
Compress-Archive -Path dist/minecraft-13animation -DestinationPath dist/minecraft-13animation-windows-x64.zip -Force
