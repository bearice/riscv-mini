"""AE350 binding of the framework upstream archiver (scripts.modular.upstream)."""
from scripts.modular import upstream


def archive(store, run, args, workspace, files):
    return upstream.archive(store, run, args, workspace, files, 'mega138k_ae350')
