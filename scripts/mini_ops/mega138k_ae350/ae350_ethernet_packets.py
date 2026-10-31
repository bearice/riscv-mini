"""Raw-wire ARP/ICMP/UDP checks on the explicitly selected direct interface.

No address, route, MAC or promiscuous-mode changes. The board test image owns
192.0.2.2/02:35:00:00:00:01; frames use 192.0.2.1 as their isolated test source.
"""
import json
import os
import random
import re
import socket
import struct
import time
from pathlib import Path

BOARD_MAC=bytes.fromhex('023500000001')
BOARD_IP=socket.inet_aton('192.0.2.2')
HOST_IP=socket.inet_aton('192.0.2.1')


def checksum(data):
    if len(data)&1:data+=b'\0'
    total=sum(struct.unpack('!%dH'%(len(data)//2),data))
    while total>>16:total=(total&0xffff)+(total>>16)
    return (~total)&0xffff


def ip_frame(mac,proto,body,sequence=0):
    header=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(body),sequence,0x4000,64,proto,0,HOST_IP,BOARD_IP)
    header=header[:10]+struct.pack('!H',checksum(header))+header[12:]
    return BOARD_MAC+mac+b'\x08\0'+header+body


def ipv4_reply(frame,mac,proto):
    if len(frame)<34 or frame[:6]!=mac or frame[6:12]!=BOARD_MAC or frame[12:14]!=b'\x08\0':return None
    packet=frame[14:];ihl=(packet[0]&15)*4;total=int.from_bytes(packet[2:4],'big')
    if packet[0]>>4!=4 or ihl<20 or ihl>60 or total<ihl or total>len(packet):return None
    if packet[9]!=proto or packet[12:16]!=BOARD_IP or packet[16:20]!=HOST_IP:return None
    if checksum(packet[:ihl]) or int.from_bytes(packet[6:8],'big')&0x3fff:return None
    return packet[ihl:total]


def exercise(interface,directory):
    if os.name!='posix' or not hasattr(socket,'AF_PACKET'):raise RuntimeError('Raw Ethernet tests require Linux')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}',interface):raise ValueError('Invalid interface name')
    root=Path('/sys/class/net')/interface
    mac=bytes.fromhex((root/'address').read_text().strip().replace(':',''))
    if (root/'carrier').read_text().strip()!='1':raise RuntimeError('Selected direct interface has no carrier')
    reports=[];rng=random.Random(0xae350)
    sock=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3))
    sock.bind((interface,0));sock.settimeout(.1)
    def exchange(name,frame,predicate,quiet=False):
        last=None;started=time.monotonic()
        for attempt in range(1 if quiet else 3):
            sock.send(frame);deadline=time.monotonic()+(.3 if quiet else 1)
            while time.monotonic()<deadline:
                try:packet=sock.recv(4096)
                except socket.timeout:continue
                if len(packet)<14 or packet[6:12]!=BOARD_MAC:continue
                if predicate(packet):
                    last=packet;break
            if last is not None:break
        passed=(last is None) if quiet else (last is not None)
        item=dict(name=name,passed=passed,attempts=attempt+1,tx=frame.hex(),rx=None if last is None else last.hex(),
                  elapsed_ms=round((time.monotonic()-started)*1000,3))
        reports.append(item)
        if not passed:raise RuntimeError('Ethernet wire check failed: '+name)
    try:
        arp=b'\xff'*6+mac+b'\x08\x06'+struct.pack('!HHBBH',1,0x800,6,4,1)+mac+HOST_IP+b'\0'*6+BOARD_IP
        def arp_ok(p):
            return len(p)>=42 and p[:6]==mac and p[12:14]==b'\x08\x06' and p[20:22]==b'\0\2' and p[22:28]==BOARD_MAC and p[28:32]==BOARD_IP and p[32:38]==mac and p[38:42]==HOST_IP
        exchange('arp',arp,arp_ok)
        for seq,n in enumerate((0,1,31,56,257,1000,1472)):
            payload=bytes(rng.randrange(256) for _ in range(n))
            body=struct.pack('!BBHHH',8,0,0,0x3501,seq)+payload
            body=body[:2]+struct.pack('!H',checksum(body))+body[4:]
            def icmp_ok(frame,payload=payload,seq=seq):
                b=ipv4_reply(frame,mac,1)
                return b is not None and len(b)==8+len(payload) and b[:2]==b'\0\0' and checksum(b)==0 and b[4:8]==struct.pack('!HH',0x3501,seq) and b[8:]==payload
            exchange('icmp-%d'%n,ip_frame(mac,1,body,seq),icmp_ok)
        last_frame=last_predicate=None
        lengths=[0,1,31,64,511,1024,1472]+[rng.randrange(1473) for _ in range(32)]
        for seq,n in enumerate(lengths):
            payload=struct.pack('!I',seq)+bytes(rng.randrange(256) for _ in range(max(0,n-4))) if n>=4 else bytes(range(n))
            body=struct.pack('!HHHH',54321,32768,8+len(payload),0)+payload
            pseudo=HOST_IP+BOARD_IP+struct.pack('!BBH',0,17,len(body))
            c=checksum(pseudo+body);body=body[:6]+struct.pack('!H',c or 0xffff)+body[8:]
            def udp_ok(frame,payload=payload):
                b=ipv4_reply(frame,mac,17)
                if b is None or len(b)!=8+len(payload):return False
                if struct.unpack('!HHH',b[:6])!=(32768,54321,len(b)) or b[8:]!=payload:return False
                return b[6:8]!=b'\0\0' and checksum(BOARD_IP+HOST_IP+struct.pack('!BBH',0,17,len(b))+b)==0
            frame=ip_frame(mac,17,body,seq)
            exchange('udp-%d-%d'%(seq,n),frame,udp_ok);last_frame,last_predicate=frame,udp_ok
        for name,ident,offset in (('bad-ip-checksum',0xf001,24),('bad-udp-checksum',0xf002,40)):
            bad=bytearray(last_frame);bad[18:20]=struct.pack('!H',ident)
            bad[24:26]=b'\0\0';bad[24:26]=struct.pack('!H',checksum(bytes(bad[14:34])))
            bad[offset]^=1
            def unexpected_reply(frame,ident=ident,payload=payload):
                b=ipv4_reply(frame,mac,17)
                return b is not None and frame[18:20]==struct.pack('!H',ident) and b[8:]==payload
            exchange(name,bytes(bad),unexpected_reply,quiet=True)
        return dict(interface=interface,host_mac=mac.hex(':'),board_mac=BOARD_MAC.hex(':'),
                    arp_passed=True,icmp_cases=7,udp_cases=len(lengths),bad_checksum_cases=2,checks=len(reports),
                    retries=sum(item['attempts']-1 for item in reports),passed=True)
    finally:
        sock.close()
        (Path(directory)/'wire-packets.json').write_text(json.dumps(reports,indent=2)+'\n')


def make_udp_frame(mac, seq, payload):
    body = struct.pack('!HHHH', 54321, 32768, 8 + len(payload), 0) + payload
    pseudo = HOST_IP + BOARD_IP + struct.pack('!BBH', 0, 17, len(body))
    c = checksum(pseudo + body)
    body = body[:6] + struct.pack('!H', c or 0xffff) + body[8:]
    return ip_frame(mac, 17, body, seq), payload


def verify_udp_reply(frame, mac, seq, payload):
    if len(frame) < 34 or int.from_bytes(frame[18:20], 'big') != (seq & 0xffff):
        return False
    b = ipv4_reply(frame, mac, 17)
    if b is None or len(b) != 8 + len(payload):
        return False
    if struct.unpack('!HHH', b[:6]) != (32768, 54321, len(b)) or b[8:] != payload:
        return False
    return b[6:8] != b'\0\0' and checksum(BOARD_IP + HOST_IP + struct.pack('!BBH', 0, 17, len(b)) + b) == 0


def uart_exchange(session, command, expected_prefix, timeout=10.0):
    session.port.write((command + '\n').encode('ascii'))
    session.port.flush()
    badframe_line = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = session.until(b'\r\n', max(0.1, deadline - time.monotonic()))
        line = raw.decode('ascii', errors='replace').strip()
        if line:
            print(line, flush=True)
        if line.startswith('ETH BADFRAME '):
            badframe_line = line
        elif line.startswith(expected_prefix):
            return line, badframe_line
    raise TimeoutError(f'Timed out waiting for {expected_prefix!r} after command {command!r}')


def parse_kv_line(line, prefix):
    parts = line[len(prefix):].strip().split()
    out = {}
    for i in range(0, len(parts) - 1, 2):
        out[parts[i]] = int(parts[i + 1], 16)
    return out


def analyze_bad_frame(badframe_line, expected_frame):
    """Compare captured first bad frame against expected wire bytes by low/high nibble and RX_ER."""
    if not badframe_line or not badframe_line.startswith('ETH BADFRAME '):
        return None
    head, _, data_part = badframe_line.partition(' DATA ')
    meta = parse_kv_line(head, 'ETH BADFRAME ')
    diag = meta.get('DIAG', 0)
    info = meta.get('INFO', 0)
    cap_len = (diag >> 16) & 0x7ff
    flags = (diag >> 8) & 0xf
    first_err_byte = info & 0x7ff
    fcs_rx_er_mask = (info >> 16) & 0xf
    captured = bytearray()
    rx_er_bytes = []
    for w_idx, token in enumerate(data_part.strip().split()):
        if ':' not in token:
            continue
        w_hex, er_hex = token.split(':', 1)
        word = int(w_hex, 16)
        er = int(er_hex, 16)
        for lane in range(4):
            b_idx = w_idx * 4 + lane
            if b_idx < cap_len:
                captured.append((word >> (8 * lane)) & 0xff)
                if (er >> lane) & 1:
                    rx_er_bytes.append(b_idx)
    compare_len = min(len(captured), len(expected_frame))
    low_nibble_mismatches = 0
    high_nibble_mismatches = 0
    lane_rising_errors = {f'rx_data{i}': 0 for i in range(4)}
    lane_falling_errors = {f'rx_data{i}': 0 for i in range(4)}
    first_mismatch = None
    sample_diffs = []
    for idx in range(compare_len):
        got = captured[idx]
        exp = expected_frame[idx]
        diff = got ^ exp
        low_xor = diff & 0x0f
        high_xor = (diff >> 4) & 0x0f
        if low_xor:
            low_nibble_mismatches += 1
            for b in range(4):
                if (low_xor >> b) & 1:
                    lane_rising_errors[f'rx_data{b}'] += 1
        if high_xor:
            high_nibble_mismatches += 1
            for b in range(4):
                if (high_xor >> b) & 1:
                    lane_falling_errors[f'rx_data{b}'] += 1
        if diff or (idx in rx_er_bytes):
            entry = dict(
                byte_offset=idx,
                expected=f'0x{exp:02x}',
                captured=f'0x{got:02x}',
                low_nibble_expected=f'0x{exp & 0xf:x}',
                low_nibble_captured=f'0x{got & 0xf:x}',
                low_nibble_xor=f'0x{low_xor:x}',
                high_nibble_expected=f'0x{(exp >> 4) & 0xf:x}',
                high_nibble_captured=f'0x{(got >> 4) & 0xf:x}',
                high_nibble_xor=f'0x{high_xor:x}',
                rx_er=idx in rx_er_bytes,
            )
            if first_mismatch is None:
                first_mismatch = entry
            if len(sample_diffs) < 16:
                sample_diffs.append(entry)
    return dict(
        captured_frame_length=cap_len,
        captured_bytes_compared=compare_len,
        flags=dict(
            rx_er=bool(flags & 1),
            fcs_error=bool(flags & 2),
            bad_be=bool(flags & 4),
            mtu_overflow=bool(flags & 8),
        ),
        first_rx_er_byte=None if first_err_byte == 0x7ff else first_err_byte,
        fcs_rx_er_mask=f'0x{fcs_rx_er_mask:x}',
        rx_er_byte_indices=rx_er_bytes[:32],
        rx_er_byte_count=len(rx_er_bytes),
        low_nibble_mismatches=low_nibble_mismatches,
        high_nibble_mismatches=high_nibble_mismatches,
        lane_rising_errors=lane_rising_errors,
        lane_falling_errors=lane_falling_errors,
        first_mismatch=first_mismatch,
        sample_diffs=sample_diffs,
        captured_prefix_hex=bytes(captured[:64]).hex(),
        expected_prefix_hex=bytes(expected_frame[:64]).hex(),
    )


def run_deterministic_burst(sock, mac, seed, payload_len, packet_count, interval_s):
    rng = random.Random(seed)
    while True:
        try:
            sock.recv(4096)
        except (socket.timeout, BlockingIOError):
            break
    frames = []
    for seq in range(packet_count):
        payload = struct.pack('!I', seq) + bytes(rng.randrange(256) for _ in range(max(0, payload_len - 4)))
        frame, _ = make_udp_frame(mac, seq, payload)
        frames.append((seq, frame, payload))
    replied = set()
    sock.setBlocking(False) if hasattr(sock, 'setBlocking') else sock.setblocking(False)
    try:
        for seq, frame, payload in frames:
            t0 = time.monotonic()
            sock.send(frame)
            while time.monotonic() - t0 < interval_s:
                try:
                    pkt = sock.recv(4096)
                except (socket.timeout, BlockingIOError):
                    time.sleep(0.0001)
                    continue
                if len(pkt) >= 34 and pkt[6:12] == BOARD_MAC:
                    r_seq = int.from_bytes(pkt[18:20], 'big')
                    if 0 <= r_seq < packet_count and r_seq not in replied:
                        if verify_udp_reply(pkt, mac, r_seq, frames[r_seq][2]):
                            replied.add(r_seq)
        drain_deadline = time.monotonic() + 0.2
        while len(replied) < packet_count and time.monotonic() < drain_deadline:
            try:
                pkt = sock.recv(4096)
            except (socket.timeout, BlockingIOError):
                time.sleep(0.0005)
                continue
            if len(pkt) >= 34 and pkt[6:12] == BOARD_MAC:
                r_seq = int.from_bytes(pkt[18:20], 'big')
                if 0 <= r_seq < packet_count and r_seq not in replied:
                    if verify_udp_reply(pkt, mac, r_seq, frames[r_seq][2]):
                        replied.add(r_seq)
    finally:
        sock.settimeout(0.1)
    first_missing_idx = next((i for i in range(packet_count) if i not in replied), 0)
    return dict(
        tx_sent=packet_count,
        rx_replies=len(replied),
        host_loss=packet_count - len(replied),
        reference_frame=frames[first_missing_idx][1],
    )


def measure_tap_point(sock, mac, session, tap_or_lanes, seed=0xae350508, payload_len=1472, packet_count=16, interval_s=0.020):
    if isinstance(tap_or_lanes, int):
        cmd = f'T {tap_or_lanes:x}'
        line, _ = uart_exchange(session, cmd, 'ETH TAP CTRL ')
        meta = parse_kv_line(line, 'ETH TAP ')
        taps = [tap_or_lanes] * 5
    else:
        ctl = tap_or_lanes[0]
        dpack = tap_or_lanes[1] | (tap_or_lanes[2] << 8) | (tap_or_lanes[3] << 16) | (tap_or_lanes[4] << 24)
        cmd = f'L {ctl:x} {dpack:08x}'
        line, _ = uart_exchange(session, cmd, 'ETH LANE CTRL ')
        meta = parse_kv_line(line, 'ETH LANE ')
        taps = list(tap_or_lanes)
    burst = run_deterministic_burst(sock, mac, seed, payload_len, packet_count, interval_s)
    snap_line, badframe_line = uart_exchange(session, 'S', 'ETH SNAP ')
    snap = parse_kv_line(snap_line, 'ETH SNAP ')
    bad_analysis = analyze_bad_frame(badframe_line, burst['reference_frame']) if badframe_line else None
    phy_idle_errors = snap.get('PHY10', 0) & 0xff
    passed = (
        burst['rx_replies'] == packet_count
        and snap.get('RX', 0) >= packet_count
        and snap.get('TX', 0) >= packet_count
        and snap.get('UDP', 0) >= packet_count
        and snap.get('DROP', 0) == 0
        and snap.get('PRXER', 0) == 0
        and snap.get('DRXER', 0) == 0
        and snap.get('DFCS', 0) == 0
        and snap.get('MCRC', 0) == 0
        and snap.get('MPRE', 0) == 0
        and snap.get('DERR', 0) == 0
        and snap.get('CSUM', 0) == 0
        and phy_idle_errors == 0
        and (snap.get('DIAG', 0) & 2) == 0
    )
    return dict(
        tap=tap_or_lanes if isinstance(tap_or_lanes, int) else None,
        lane_taps=dict(ctl=taps[0], d0=taps[1], d1=taps[2], d2=taps[3], d3=taps[4]),
        delay_ps=round(taps[0] * 12.5, 1) if isinstance(tap_or_lanes, int) else [round(x * 12.5, 1) for x in taps],
        ctrl_reg=f"0x{meta.get('CTRL', 0):08x}",
        data_reg=f"0x{meta.get('DATA', 0):08x}",
        tx_sent=burst['tx_sent'],
        rx_replies=burst['rx_replies'],
        host_loss=burst['host_loss'],
        board_rx_ok=snap.get('RX', 0),
        board_tx_ok=snap.get('TX', 0),
        phy_rx_frames=snap.get('PRX', 0),
        phy_rx_er_frames=snap.get('PRXER', 0),
        dma_rx_er_frames=snap.get('DRXER', 0),
        dma_fcs_errors=snap.get('DFCS', 0),
        mac_crc_errors=snap.get('MCRC', 0),
        mac_preamble_errors=snap.get('MPRE', 0),
        mac_drops=snap.get('MDROP', 0),
        dma_drops=snap.get('DDROP', 0),
        total_drops=snap.get('DROP', 0),
        bad_checksum_rejected=snap.get('CSUM', 0),
        phy_reg10=f"0x{snap.get('PHY10', 0):04x}",
        phy_idle_errors=phy_idle_errors,
        diag_locked=bool(snap.get('DIAG', 0) & 2),
        bad_frame=bad_analysis,
        passed=passed,
    )


def contiguous_pass_window(points_by_tap):
    sorted_taps = sorted(points_by_tap.keys())
    best_run = []
    current_run = []
    for t in sorted_taps:
        if points_by_tap[t]['passed']:
            current_run.append(t)
            if len(current_run) > len(best_run):
                best_run = list(current_run)
        else:
            current_run = []
    if not best_run:
        return None
    low = best_run[0]
    high = best_run[-1]
    center = (low + high) // 2
    return dict(
        min_pass_tap=low,
        max_pass_tap=high,
        min_pass_delay_ps=round(low * 12.5, 1),
        max_pass_delay_ps=round(high * 12.5, 1),
        window_taps=high - low,
        window_ps=round((high - low) * 12.5, 1),
        center_tap=center,
        center_delay_ps=round(center * 12.5, 1),
    )


def sweep_rx_delay(interface, session, directory, per_lane=False):
    root = Path('/sys/class/net') / interface
    mac = bytes.fromhex((root / 'address').read_text().strip().replace(':', ''))
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    sock.bind((interface, 0))
    sock.settimeout(0.1)
    try:
        err_line, _ = uart_exchange(session, 'T f1', 'ETH TAP ')
        range_guard_verified = err_line.strip() == 'ETH TAP ERR'
        points = {}
        coarse_taps = list(range(0, 241, 16))
        for t in coarse_taps:
            points[t] = measure_tap_point(sock, mac, session, t)
        fine_taps = set()
        for a, b in zip(coarse_taps, coarse_taps[1:]):
            if points[a]['passed'] != points[b]['passed']:
                for t in range(a + 2, b, 2):
                    fine_taps.add(t)
        for t in sorted(fine_taps):
            if t not in points:
                points[t] = measure_tap_point(sock, mac, session, t)
        window = contiguous_pass_window(points)
        lane_windows = {}
        if per_lane and window is not None:
            center = window['center_tap']
            lane_names = ('rx_ctl', 'rx_data0', 'rx_data1', 'rx_data2', 'rx_data3')
            for lane_idx, lane_name in enumerate(lane_names):
                lane_pts = {}
                for t in coarse_taps:
                    taps = [center] * 5
                    taps[lane_idx] = t
                    lane_pts[t] = measure_tap_point(sock, mac, session, taps, packet_count=16)
                for a, b in zip(coarse_taps, coarse_taps[1:]):
                    if lane_pts[a]['passed'] != lane_pts[b]['passed']:
                        for t in range(a + 4, b, 4):
                            taps = [center] * 5
                            taps[lane_idx] = t
                            lane_pts[t] = measure_tap_point(sock, mac, session, taps, packet_count=16)
                lane_windows[lane_name] = dict(
                    window=contiguous_pass_window(lane_pts),
                    points=[lane_pts[k] for k in sorted(lane_pts.keys())],
                )
        selected_tap = window['center_tap'] if window is not None else 200
        uart_exchange(session, f'T {selected_tap:x}', 'ETH TAP CTRL ')
        bad_frame_examples = [
            dict(tap=t, delay_ps=points[t]['delay_ps'], bad_frame=points[t]['bad_frame'])
            for t in sorted(points.keys())
            if points[t]['bad_frame'] is not None
        ]
        result = dict(
            parameters=dict(
                seed=0xae350508,
                payload_length=1472,
                wire_frame_length=1514,
                packet_count=16,
                interval_ms=20.0,
                tap_step_ps=12.5,
                allowed_tap_range=[0, 240],
                range_guard_verified=range_guard_verified,
                pvt_note='Room-temperature single-board window measurement; does not substitute full PVT qualification.',
            ),
            common_window=window,
            selected_center_tap=selected_tap,
            selected_center_delay_ps=round(selected_tap * 12.5, 1),
            bad_frame_captures=bad_frame_examples,
            common_points=[points[k] for k in sorted(points.keys())],
            per_lane=lane_windows if per_lane else None,
            passed=window is not None and window['window_ps'] >= 500.0 and range_guard_verified,
        )
        (Path(directory) / 'rx-delay-sweep.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
    finally:
        sock.close()

def exercise_sustained_long_frames(interface, session, directory, frame_count=256, payload_len=1472, interval_s=0.020):
    root = Path('/sys/class/net') / interface
    mac = bytes.fromhex((root / 'address').read_text().strip().replace(':', ''))
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    sock.bind((interface, 0))
    sock.settimeout(0.1)
    try:
        uart_exchange(session, 'R', 'ETH RESET CTRL ')
        started = time.monotonic()
        burst = run_deterministic_burst(sock, mac, 0xae350509, payload_len, frame_count, interval_s)
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        snap_line, badframe_line = uart_exchange(session, 'S', 'ETH SNAP ')
        snap = parse_kv_line(snap_line, 'ETH SNAP ')
        phy_idle_errors = snap.get('PHY10', 0) & 0xff
        passed = (
            burst['rx_replies'] == frame_count
            and snap.get('RX', 0) >= frame_count
            and snap.get('TX', 0) >= frame_count
            and snap.get('UDP', 0) >= frame_count
            and snap.get('DROP', 0) == 0
            and snap.get('PRXER', 0) == 0
            and snap.get('DRXER', 0) == 0
            and snap.get('DFCS', 0) == 0
            and snap.get('MCRC', 0) == 0
            and snap.get('MPRE', 0) == 0
            and snap.get('DERR', 0) == 0
            and phy_idle_errors == 0
            and badframe_line is None
        )
        report = dict(
            seed=0xae350509,
            payload_length=payload_len,
            wire_frame_length=42 + payload_len,
            frame_count=frame_count,
            interval_ms=round(interval_s * 1000, 3),
            elapsed_ms=elapsed_ms,
            tx_sent=burst['tx_sent'],
            rx_replies=burst['rx_replies'],
            host_loss=burst['host_loss'],
            board_rx_ok=snap.get('RX', 0),
            board_tx_ok=snap.get('TX', 0),
            phy_rx_er_frames=snap.get('PRXER', 0),
            dma_rx_er_frames=snap.get('DRXER', 0),
            dma_fcs_errors=snap.get('DFCS', 0),
            mac_crc_errors=snap.get('MCRC', 0),
            mac_preamble_errors=snap.get('MPRE', 0),
            total_drops=snap.get('DROP', 0),
            phy_idle_errors=phy_idle_errors,
            ctrl_reg=f"0x{snap.get('CTRL', 0):08x}",
            data_reg=f"0x{snap.get('DATA', 0):08x}",
            passed=passed,
        )
        (Path(directory) / 'sustained-packets.json').write_text(json.dumps(report, indent=2) + '\n')
        uart_exchange(session, 'R', 'ETH RESET CTRL ')
        if not passed:
            raise RuntimeError('Sustained long-frame check failed: ' + json.dumps(report))
        return report
    finally:
        sock.close()
