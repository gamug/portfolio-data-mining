import os

import src.config as config

def check_repository():
    for path in config.general['paths'].values():
        os.makedirs(path, exist_ok=True)