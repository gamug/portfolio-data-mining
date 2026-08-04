import os, shutil
from .commons.utils import check_repository
from .config import general

check_repository()

# copy S&P500 CSV file to input folder
shutil.copyfile(os.path.join('s&p500', 's&p500.csv'), os.path.join('..', 'input', 's&p500.csv')) # shutil.copyfile(general['paths']['input'] + '/s&p500/s&p500.csv', general['paths']['input'] + '/s&p500.csv')