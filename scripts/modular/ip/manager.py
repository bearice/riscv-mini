"""Declare IP nodes before final RTL input capture; Ninja owns their scheduling."""
from pathlib import Path
import json
import sys
from scripts.modular.storage import canonical, digest, file_hash, read, text_file, write
from scripts.modular.graph import Action


def generator_inputs(root, definition, tools, environment):
    """Capture the templates/plugins actually used, including installed tools."""
    import os
    paths = [Path(__file__), root / 'scripts/modular/storage.py',
             root / 'scripts/modular/environment.py', root / 'requirements.lock']
    if definition['generator'] == 'vexriscv':
        import pythondata_cpu_vexriscv
        base = Path(pythondata_cpu_vexriscv.data_location)
        upstream = Path(environment.get('VEXRISCV_SOURCE') or os.environ.get('VEXRISCV_SOURCE', base / 'ext/VexRiscv'))
        paths += [root / 'scripts/builders/cpu_generator.py', root / 'scripts/modular/ip/vexriscv.py']
        for tree in (base, upstream):
            paths.append(tree / 'build.sbt')
            for folder in ('src', 'project'):
                paths.extend(path for path in (tree / folder).rglob('*')
                             if path.is_file() and 'target' not in path.relative_to(tree).parts)
    else:
        paths.append(root / 'scripts/modular/ip/gowin.py')
        paths.append(root / 'scripts/modular/gowin.py')
        if definition['generator'] == 'gowin_ae350':
            paths.append(root / 'scripts/modular/ip/ae350.py')
        home = environment.get('GOWIN_ROOT') or environment.get('GOWIN_HOME')
        ide = Path(home) / 'IDE' if home else Path(tools['gowin'][0]).resolve().parents[1]
        if not (ide / 'ipcore').is_dir():
            raise ValueError('Gowin IP generation requires an installation with IDE/ipcore; set GOWIN_ROOT')
        from scripts.modular.ip.gowin import IP_IDS
        identifier = IP_IDS[definition['generator']]
        import xml.etree.ElementTree as ET
        matches = []
        for spec in (ide / 'ipcore').rglob('*.ipspec'):
            document = ET.parse(spec).getroot()
            if any(element.get('id') == identifier for element in document.iter()):
                matches.append(spec.parent)
        if len(matches) != 1:
            raise ValueError('Cannot uniquely identify installed Gowin IP plugin: ' + identifier)
        paths.extend(path for path in matches[0].rglob('*') if path.is_file() and 'doc' not in path.relative_to(matches[0]).parts)
        paths.extend(path for folder in ('bin', 'lib') for path in (ide / folder).iterdir()
                     if path.is_file() and (path.suffix in ('.so', '.dll') or path.name in ('gw_sh', 'gw_sh.exe', 'GowinModGen', 'GowinModGen.exe')))
        if definition['generator'] != 'gowin_ae350':
            paths.extend(path for path in (ide / 'plugins/ide').glob('*') if path.is_file())
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError('Missing IP generation input: ' + ', '.join(missing))
    return {str(path.absolute()): file_hash(path) for path in sorted(set(paths))}


def declare(graph, machine, tools, environment):
    manifests = {}
    for name, definition in machine['machine']['ip'].items():
        needed = ('java', 'sbt-launch') if definition['generator']=='vexriscv' else ('gowin',)
        selected_tools = {key: tools[key] for key in needed}
        output = graph.work / 'generated' / name
        manifest = output / 'ip-manifest.json'
        generator = graph.root / 'scripts/modular/ip' / ('vexriscv.py' if definition['generator']=='vexriscv' else 'gowin.py')
        board = {} if definition['generator']=='vexriscv' else {'fpga': machine['board']['fpga']}
        source_identity = generator_inputs(graph.root, definition, selected_tools, environment)
        tool_identity = {role: file_hash(binding[0]) for role, binding in selected_tools.items()}
        recipe = dict(schema=1, root=str(graph.root), board=board,
                      instance=name, definition=definition, output=str(output),
                      build_directory=str(graph.work / 'generator' / name),
                      tools=selected_tools, environment=environment, source_identity=source_identity, tool_identity=tool_identity)
        request = graph.work / 'ip' / (name + '.json')
        text_file(request, canonical(recipe).decode() + '\n')
        if manifest.is_file():
            try:
                record = read(manifest)
                valid = (record.get('schema') == 1 and record.get('definition') == definition
                         and bool(record.get('files')) and all(
                             (output / path).is_file() and file_hash(output / path) == sha
                             for path, sha in record['files'].items()))
            except (OSError, ValueError, TypeError, AttributeError):
                valid = False
            if not valid:
                manifest.unlink()
        inputs = [request, generator, Path(__file__), graph.root / 'scripts/modular/worker.py']
        inputs += [Path(path) for binding in selected_tools.values() for path in binding[:1]]
        graph.add(Action('ip-' + name,
            (sys.executable, graph.root / 'scripts/mini.py', '_worker', 'vendor-ip', request),
            tuple(inputs), (manifest,), parameters=definition, pool='vendor'))
        manifests[name] = manifest
    return manifests


def generate(recipe_path):
    import tempfile
    import shutil
    from contextlib import nullcontext
    from scripts.modular.storage import source_bytes
    recipe = read(recipe_path)
    if generator_inputs(Path(recipe['root']), recipe['definition'], recipe['tools'], recipe['environment']) != recipe['source_identity']:
        raise ValueError('IP generator inputs changed after graph declaration')
    output = Path(recipe['output'])
    output.parent.mkdir(parents=True, exist_ok=True)
    definition = recipe['definition']
    if definition['generator'] == 'vexriscv':
        Path(recipe['build_directory']).mkdir(parents=True, exist_ok=True)
        staging = nullcontext(recipe['build_directory'])
    else:
        staging = tempfile.TemporaryDirectory(prefix=output.name + '-', dir=output.parent)
    with staging as temporary:
        stage = Path(temporary)
        publish = stage / 'publish'
        if publish.exists():
            shutil.rmtree(publish)
        if definition['generator'] == 'vexriscv':
            from scripts.modular.ip.vexriscv import generate as run
            primary = run(definition, stage, recipe['tools'], recipe['environment'])
        else:
            from scripts.modular.ip.gowin import generate as run
            try:
                primary = run(definition, recipe['board'], stage, recipe['tools']['gowin'], recipe['environment'])
            finally:
                logs = Path(recipe['build_directory'])
                logs.mkdir(parents=True, exist_ok=True)
                for name in ('generate.log', 'generate.tcl', 'create.tcl', 'native-defaults.tcl'):
                    if (stage / name).is_file():
                        shutil.copyfile(stage / name, logs / name)
                for source in (*stage.rglob('*.ipc'), *stage.rglob('*.gprj')):
                    shutil.copyfile(source, logs / source.name)
                if (stage / 'logs').is_dir():
                    shutil.copytree(stage / 'logs', logs / 'logs', dirs_exist_ok=True)
        extensions = {'.v','.sv','.vh','.vhd','.ipc','.init','.json','.yaml'}
        files = {path.relative_to(stage).as_posix(): path for path in stage.rglob('*')
                 if path.is_file() and (path.suffix in extensions or path.parent.name == 'itcm_initial')
                 and (path == primary or not path.name.endswith('_tmp.v'))
                 and (path == primary or not any(part in
                     ('target','obj','ivy','sbt-global','coursier','sbt-boot','ext','project','logs','temp')
                     for part in path.relative_to(stage).parts))}
        if primary.relative_to(stage).as_posix() not in files:
            raise ValueError('Primary vendor output is missing')
        publish.mkdir()
        hashes = {}
        for name, source in files.items():
            data = source_bytes(source)
            # Generated text must not embed the random staging directory.
            data = data.replace(stage.as_posix().encode(), b'.')
            destination = publish / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.is_file() or destination.read_bytes() != data:
                destination.write_bytes(data)
            hashes[name] = file_hash(destination)
        record = dict(schema=1, definition=definition,
              module=definition['module'], primary=primary.relative_to(stage).as_posix(), files=hashes,
              input_identity=dict(board=recipe['board'], sources=recipe['source_identity'], tools=recipe['tool_identity']),
              identity=digest(dict(definition=definition, board=recipe['board'], files=hashes, sources=recipe['source_identity'], tools=recipe['tool_identity'])))
        if generator_inputs(Path(recipe['root']), definition, recipe['tools'], recipe['environment']) != recipe['source_identity']:
            raise ValueError('IP generator inputs changed during generation')
        write(publish / 'ip-manifest.json', record)
        current = output / 'ip-manifest.json'
        if current.is_file() and read(current) == record and all((output/name).is_file() and file_hash(output/name)==sha for name,sha in hashes.items()):
            return 0
        import os, uuid
        previous = output.with_name(output.name + '.previous-' + uuid.uuid4().hex)
        if output.exists():
            os.replace(output, previous)
        try:
            os.replace(publish, output)
        except BaseException:
            if previous.exists() and not output.exists():
                os.replace(previous, output)
            raise
        if previous.exists():
            shutil.rmtree(previous)
    return 0


def ensure(store, run, machine_name, plan=False):
    """Resolve IP inputs before calling any RTL/firmware backend."""
    import os
    import subprocess
    from gateware.description import load_machine
    from scripts.modular.environment import discover, identity, process_environment
    from scripts.modular.workspace import working
    from scripts.modular.graph import Graph
    board, machine = load_machine(machine_name, store.root)
    snapshot = dict(board=board, machine=machine)
    roles = {'python', 'ninja'}
    for definition in machine['ip'].values():
        roles.update(('java', 'sbt-launch') if definition['generator']=='vexriscv' else ('gowin',))
    environment = discover(roles, store.root)
    execution = {key: os.environ[key] for key in ('GOWIN_ROOT','GOWIN_HOME','JAVA_HOME','SBT_LAUNCH_JAR','VEXRISCV_SOURCE','QT_QPA_PLATFORM') if key in os.environ}
    request_key = digest(dict(configuration=snapshot, tools=identity(environment)))
    if plan:
        for name in machine['ip']:
            run.step('ip-' + name, 'PLAN', reason='JSON parameters and generator/tool identities select the IP input')
        return None
    with working(store, run, machine_name, 'vendor-ip', request_key) as workspace:
        graph = Graph(store.root, workspace.path)
        manifests = declare(graph, snapshot, environment['bindings'], execution)
        graph_file = graph.compile(['ip-' + name for name in manifests])
        log_path = run.path / 'vendor-ip.log'
        with log_path.open('w') as log:
            subprocess.run([*environment['bindings']['ninja'], '-f', str(graph_file)],
                cwd=store.root, env=process_environment(execution), stdout=log, stderr=subprocess.STDOUT, check=True)
        if load_machine(machine_name, store.root) != (board, machine):
            raise ValueError('Machine configuration changed during IP generation')
        artifacts = {}
        for name, manifest in manifests.items():
            record = read(manifest)
            files = {'outputs/' + filename: manifest.parent / filename for filename in record['files']}
            files['ip-manifest.json'] = manifest
            ref = store.lookup('vendor-ip', record['identity'])
            if ref is None:
                ref = store.publish('vendor-ip', record['identity'], files, config=record)
            else:
                store.get(ref)
            artifacts[name] = dict(artifact=ref, module=record['module'], primary=record['primary'],
                directory=(store.path(ref) / 'outputs').relative_to(store.root).as_posix(),
                files=record['files'], identity=record['identity'])
        workspace.success()
    return dict(**snapshot, artifacts=artifacts)
