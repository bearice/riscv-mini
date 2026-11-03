"""Archive upstream delivery files; mutable compiler trees stay in work."""
import shutil
from scripts.modular.storage import digest, file_hash


def archive(store, run, args, workspace, files):
    config = dict(machine='mega138k_ae350', target=args.target,
                  files={name:file_hash(path) for name,path in files.items()})
    artifact = store.publish('backend/'+args.target, digest(config),
                             {'outputs/'+name:path for name,path in files.items()},
                             config=config, dependencies={})
    workspace.success(artifact)
    delivery = store.path(artifact)/'outputs'
    if args.output_dir:
        shutil.copytree(delivery, args.output_dir.resolve(), dirs_exist_ok=True)
    return artifact, delivery
