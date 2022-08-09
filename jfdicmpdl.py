#!/usr/bin/env python3
# CurseForge modpack downloader
# This program is an alternative to the Twitch client, written for Linux users,
# so that they can install Minecraft modpacks from CurseForge.
# This tool requires that the user download the pack zip from CurseForge. It
# will then generate a complete modpack directory that can be imported into
# a launcher of the user's choice.
#
# Please see the included README file for more info.

import mod_download
import os
import sys
import json
import subprocess
import time
import random
import shutil
import argparse
from distutils.dir_util import copy_tree
from zipfile import ZipFile

def get_user_mcdir():
    return os.getenv('HOME') + '/.minecraft'

def main(zipfile, user_mcdir=None):
    if user_mcdir is None:
        user_mcdir = get_user_mcdir()

    # Extract pack
    packname = os.path.splitext(zipfile)[0]
    packname = os.path.basename(packname)
    packdata_dir = '.packs/' + packname
    if os.path.isdir(packdata_dir):
        print("[pack data already unzipped]")
    else:
        if not os.path.isdir('.packs/'):
            os.mkdir('.packs')
        print("Extracting %s" % zipfile)
        with ZipFile(zipfile, 'r') as zip:
            zip.extractall(packdata_dir)

    # Generate minecraft environment
    mc_dir = 'packs/' + packname + '/.minecraft'
    if os.path.isdir(mc_dir):
        print("[minecraft dir already created]")
    else:
        print("Creating .minecraft directory")
        if not os.path.isdir('packs/'):
            os.mkdir('packs/')
        if not os.path.isdir('packs/' + packname):
            os.mkdir('packs/' + packname)
        os.mkdir(mc_dir)

        print("Creating symlinks")
        if not os.path.isdir('global/'):
            os.mkdir('global')
            os.mkdir('global/libraries')
            os.mkdir('global/resourcepacks')
            os.mkdir('global/saves')
            os.mkdir('global/shaderpacks')
            os.mkdir('global/assets')

        os.symlink(os.path.abspath('global/libraries'), mc_dir + '/libraries', True)
        os.symlink(os.path.abspath('global/resourcepacks'), mc_dir + '/resourcepacks', True)
        os.symlink(os.path.abspath('global/saves'), mc_dir + '/saves', True)
        os.symlink(os.path.abspath('global/shaderpacks'), mc_dir + '/shaderpacks', True)
        os.symlink(os.path.abspath('global/assets'), mc_dir + '/assets', True)

    try:
        with open(packdata_dir + '/manifest.json', 'r') as mf:
            manifest = json.load(mf)
    except (json.JsonDecodeError, OSError) as e:
        print("Manifest file not found or was corrupted.")
        print(e)
        return

    ml_message = 'You need to install: '
    for modloader in manifest['minecraft']['modLoaders']:
        ml_message = ml_message + modloader['id'] + " "

    # Download mods
    if not os.path.exists(mc_dir + '/.mod_success'):
        if not os.path.isdir(mc_dir + '/mods'):
            os.mkdir(mc_dir + '/mods')
        print("Downloading mods")
        if not os.path.isdir('.modcache'):
            os.mkdir('.modcache')

        # if not os.path.isdir('node_modules'):
        #     print("Installing NodeJS dependencies")
        #     subprocess.run(['npm', 'install'])
        # subprocess.run(['node', 'mod_download.js', packdata_dir + '/manifest.json', '.modcache', packdata_dir + '/mods.json'])

        mods, manual_downloads = mod_download.main(packdata_dir + '/manifest.json', '.modcache')
        if len(manual_downloads) > 0:
            while True:
                actual_manual_dls = [] # which ones aren't already downloaded
                for url, resp in manual_downloads:
                    outfile = resp[3]
                    if not os.path.exists(outfile):
                        actual_manual_dls.append((url, outfile))
                if len(actual_manual_dls) > 0:
                    print("====MANUAL DOWNLOAD REQUIRED====")
                    print("The following mods cannot be downloaded due to the new Project Distribution Toggle.")
                    print("Please download them manually; the files will be retrieved from your downloads directly.")
                    for url, outfile in actual_manual_dls:
                        print("* %s (%s)" % (url, os.path.basename(outfile)))
                    
                    # TODO save user's configured downloads folder somewhere
                    user_downloads_dir = os.environ['HOME'] + '/Downloads'
                    print("Retrieving downloads from %s - if that isn't your browser's download location, enter" \
                            % user_downloads_dir)
                    print("the correct location below. Otherwise, press Enter to continue.")
                    req_downloads_dir = input()

                    req_downloads_dir = os.path.expanduser(req_downloads_dir)
                    if len(req_downloads_dir) > 0:
                        if not os.path.isdir(req_downloads_dir):
                            print("- input directory is not a directory; ignoring")
                        else:
                            user_downloads_dir = req_downloads_dir
                    print("Finding files in %s..." % user_downloads_dir)
                    
                    for url, outfile in actual_manual_dls:
                        fname = os.path.basename(outfile).replace(' ', '+')
                        dl_path = user_downloads_dir + '/' + fname
                        if os.path.exists(dl_path):
                            print(dl_path)
                            shutil.move(dl_path, outfile)
                else:
                    break

        # Link mods
        print("Linking mods")
        if not os.path.isdir(mc_dir + '/resources'):
            os.mkdir(mc_dir + '/resources')

        for mod in mods:
            jar = mod[0]
            type = mod[1]
            if type == 'mc-mods':
                modfile = mc_dir + '/mods/' + os.path.basename(jar)
                if not os.path.exists(modfile):
                    os.symlink(os.path.abspath(jar), modfile)
            elif type == 'texture-packs':
                print("Extracting texture pack %s" % jar)
                texpack_dir = '/tmp/%06d' % random.randint(0, 999999)
                os.mkdir(texpack_dir)
                with ZipFile(jar, 'r') as zip:
                    zip.extractall(texpack_dir)
                for dir in os.listdir(texpack_dir + '/assets'):
                    f = texpack_dir + '/assets/' + dir
                    if os.path.isdir(f):
                        copy_tree(f, mc_dir + '/resources/' + dir)
                    else:
                        shutil.copyfile(f, mc_dir + '/resources/' + dir)
                shutil.rmtree(texpack_dir)
            else:
                print("Unknown file type %s" % type)
                sys.exit(1)

    # Create success marker
    with open(mc_dir + '/.mod_success', 'wb') as f:
        pass

    # Copy overrides
    print("Copying overrides")
    for dir in os.listdir(packdata_dir + '/overrides'):
        print(dir + "...")
        if os.path.isdir(packdata_dir + '/overrides/' + dir):
            copy_tree(packdata_dir + '/overrides/' + dir, mc_dir + '/' + dir)
        else:
            shutil.copyfile(packdata_dir + '/overrides/' + dir, mc_dir + '/' + dir)
    print("Done!")
    print()
    print()
    print()
    print("The modpack has been downloaded")
    print(ml_message)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('zipfile')
    parser.add_argument('--mcdir', dest='mcdir')
    args = parser.parse_args(sys.argv[1:])
    main(args.zipfile, args.mcdir)
