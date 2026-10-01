"""Recording analysis must distinguish persistent tones from DC after mute."""
import array
import math
import sys
import tempfile
import wave
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audio_capture import analyze

with tempfile.TemporaryDirectory() as directory:
    for muted in (True,False):
        pcm=array.array('h')
        for frame in range(48000*4):
            for hz in (46875/64,46875/96):
                # This DC makes a raw-RMS mute comparison fail even when the
                # PCM tone has stopped. A leaked tone must still be rejected.
                tone=600*math.sin(2*math.pi*hz*frame/48000) if frame<96000 or not muted else 0
                pcm.append(round(tone+800))
        path=Path(directory)/'capture.wav'
        with wave.open(str(path),'wb') as wav:
            wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(48000);wav.writeframes(pcm.tobytes())
        result=analyze(path)
        for channel in result['channels']:
            assert channel['silent_rms']>channel['rms']/4,'Fixture must reproduce misleading RMS'
            assert (channel['mute_tone_attenuation_db']>=40)==muted,channel
            assert abs(channel['peak_hz']-channel['expected_hz'])<.2,channel
            assert channel['separation_db']>30
print('Line-in analysis PASS: DC survives mute, real test-tone suppression required, leaked tone rejected')
