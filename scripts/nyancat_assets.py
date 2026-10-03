"""Convert the referenced Nyan Cat GIF and BGM into a BIOS payload's assets.

Downloads are cached in the output directory. Source media remain owned by
their original creators; this script does not grant a redistribution license.
The board plays lossless pixel-art frames and 48 kHz mono PCM in both channels.
"""
import argparse, hashlib, json, subprocess, urllib.request
from pathlib import Path
from PIL import Image
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {'original.gif': 'https://www.nyan.cat/cats/original.gif',
           'original.mp3': 'https://www.nyan.cat/music/original.mp3'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'build/nyancat')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    media = out/'assets'
    media.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        target = media/name
        if not target.exists():
            with urllib.request.urlopen(url, timeout=30) as response:
                target.write_bytes(response.read())

    gif = Image.open(media/'original.gif')
    palette = [(0, 0, 0, 0)]
    frames, durations = [], []
    # Original artwork is on an 8x8 grid. Verify this, rather than resampling
    # away arbitrary detail from a changed source asset.
    for index in range(gif.n_frames):
        gif.seek(index)
        rgba = gif.convert('RGBA')
        assert rgba.size == (272, 168), 'Unexpected GIF dimensions'
        frame = []
        for y in range(0, 168, 8):
            for x in range(0, 272, 8):
                pixels = list(rgba.crop((x, y, x+8, y+8)).get_flattened_data())
                assert all(p == pixels[0] for p in pixels), 'GIF is not on the original 8x8 grid'
                color = pixels[0] if pixels[0][3] else palette[0]
                if color not in palette:
                    palette.append(color)
                frame.append(palette.index(color))
        frames.append(frame)
        durations.append(gif.info.get('duration', 70))
    assert len(palette) <= 256 and all(durations)
    colors = [(r >> 3) << 11 | (g >> 2) << 5 | (b >> 3) for r, g, b, _ in palette]
    header = '#pragma once\n#include <stdint.h>\n'
    header += f'#define NYAN_FRAMES {len(frames)}u\n#define NYAN_COLS 34u\n#define NYAN_ROWS 21u\n'
    header += 'static const uint16_t nyan_palette[]={' + ','.join(hex(c) for c in colors) + '};\n'
    header += 'static const uint16_t nyan_duration[]={' + ','.join(map(str, durations)) + '};\n'
    header += 'static const uint8_t nyan_frames[NYAN_FRAMES][NYAN_COLS*NYAN_ROWS]={\n'
    header += ',\n'.join('{' + ','.join(map(str, frame)) + '}' for frame in frames) + '\n};\n'
    header += 'extern const int16_t nyan_pcm[],nyan_pcm_end[];\n'
    (out/'nyancat_assets.h').write_text(header, encoding='utf-8')
    pcm = out/'music.s16'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y',
                    '-i', str(media/'original.mp3'), '-ac', '1', '-ar', '48000',
                    '-af', 'volume=0.22', '-f', 's16le', str(pcm)], check=True)
    assert pcm.stat().st_size % 2 == 0
    assembly = '.section .rodata.nyan_music,"a",@progbits\n.balign 4\n'
    assembly += '.global nyan_pcm,nyan_pcm_end\nnyan_pcm:\n'
    assembly += f'.incbin "{pcm.as_posix()}"\nnyan_pcm_end:\n'
    (out/'assets.S').write_text(assembly, encoding='utf-8')
    manifest = {'source_page': 'https://www.nyan.cat/index.php?cat=original',
                'sources': {n: {'url': u, 'sha256': hashlib.sha256((media/n).read_bytes()).hexdigest()}
                            for n, u in SOURCES.items()},
                'gif_frames': len(frames), 'frame_ms': durations, 'palette_colors': len(palette),
                'pcm_rate': 48000, 'pcm_samples': pcm.stat().st_size//2,
                'pcm_sha256': hashlib.sha256(pcm.read_bytes()).hexdigest()}
    (out/'assets.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    # A host preview uses the same source palette/grid and logical geometry.
    preview = Image.new('RGB', (480, 272), '#003366')
    rainbow = ['#ff0000', '#ff9900', '#ffff00', '#33ff00', '#0099ff', '#6633ff']
    from PIL import ImageDraw
    draw = ImageDraw.Draw(preview)
    for band, color in enumerate(rainbow):
        draw.rectangle((0, 76+band*20, 160, 95+band*20), fill=color)
    gif.seek(0)
    preview.paste(gif.convert('RGBA'), (104, 52), gif.convert('RGBA'))
    preview.save(out/'preview.png')
    print(f'Assets: {len(frames)} frames, {len(palette)} colors, '
          f'{manifest["pcm_samples"]/48000:.3f}s music')


if __name__ == '__main__':
    main()
