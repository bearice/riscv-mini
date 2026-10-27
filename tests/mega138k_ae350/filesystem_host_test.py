"""Run actual FatFs and native BIOS file services against a FAT16 RAM disk."""
from pathlib import Path
import shutil
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[2]
bsp=ROOT/'firmware/bsp/mega138k_ae350'
fatfs=ROOT/'firmware/vendor/fatfs'
with tempfile.TemporaryDirectory(prefix='ae350-fs-') as tmp:
    output=Path(tmp)/'test'
    command=[shutil.which('cc'),'-std=c11','-O2','-Wall','-Wextra','-Werror',
        '-I'+str(bsp/'bios'),'-I'+str(bsp/'sd'),'-I'+str(ROOT/'firmware/bios/include'),'-I'+str(fatfs),
        str(Path(__file__).with_suffix('.c')),str(bsp/'bios/filesystem.c'),str(bsp/'sd/diskio.c'),
        str(fatfs/'ff.c'),str(fatfs/'ffunicode.c'),'-o',str(output)]
    subprocess.run(command,check=True)
    subprocess.run([str(output)],check=True,timeout=30)
