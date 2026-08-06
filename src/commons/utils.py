import os
from enum import Enum

import src.config as config

def check_repository():
    for path in config.general['paths'].values():
        os.makedirs(path, exist_ok=True)

class FileEnumFactory:
    def __init__(self, base_path: str):
        self.base_path = base_path

    def get_files(self, exclude_keyword: str = "farmed"):
        return [
            f for f in os.listdir(self.base_path)
            if exclude_keyword not in f
        ]

    def build_enum(self, name: str = "File"):
        files = self.get_files()
        return Enum(name, {c: c for c in files})