"""One build configuration shared by gateware and firmware selection."""
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Features:
    flash: bool = True
    spi_lcd: bool = True
    sd: bool = True
    filesystem: bool = True
    video: bool = True
    board_io: bool = True
    ws2812: bool = True
    audio: bool = True
    mic: bool = True
    mic_stereo: bool = True
    eth: bool = True
    usb: bool = True

    @classmethod
    def names(cls):
        return tuple(f.name for f in fields(cls))

    @classmethod
    def resolve(cls, profile='full', overrides=None):
        values={name: profile=='full' or name=='flash' for name in cls.names()}
        values.update({k:v for k,v in (overrides or {}).items() if v is not None})
        for child,parent in [('filesystem','sd'),('mic_stereo','mic'),('eth','flash')]:
            if values[child] and not values[parent]:
                # Explicitly disabling a prerequisite also disables its default child.
                if (overrides or {}).get(child) is True:
                    raise ValueError(f'{child} requires {parent}')
                values[child]=False
        return cls(**values)

    def as_dict(self):
        return {name:getattr(self,name) for name in self.names()}

    def arguments(self):
        return [f'--{"with" if value else "without"}-{name.replace("_","-")}'
                for name,value in self.as_dict().items()]

    def header(self):
        enabled=' '.join(name for name,value in self.as_dict().items() if value)
        return '#pragma once\n'+''.join(f'#define MINI_FEATURE_{name.upper()} {int(value)}\n'
                                       for name,value in self.as_dict().items())+f'#define MINI_FEATURES_TEXT "{enabled}"\n'
