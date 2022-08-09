#!/usr/bin/env python3
# CurseForge modpack downloader
# This program is an alternative to the Twitch client, written for Linux users,
# so that they can install Minecraft modpacks from CurseForge.
# This tool requires that the user download the pack zip from CurseForge. It
# will then generate a complete modpack directory that can be imported into
# a launcher of the user's choice.
#
# Please see the included README file for more info.

import os
import sys
import requests
import json
import asyncio
import subprocess
import time
import random
import shutil
import argparse
from concurrent.futures import ThreadPoolExecutor
from distutils.dir_util import copy_tree
from zipfile import ZipFile


API_URL = 'https://api.modpacks.ch/public'


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

        mods, manual_downloads = download_all_mods(packdata_dir + '/manifest.json', '.modcache')
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
    override_dir = packdata_dir + '/overrides/'
    if os.path.exists(override_dir):
        print("Copying overrides")
        for dir in os.listdir(override_dir):
            print(dir + "...")
            if os.path.isdir(override_dir + dir):
                copy_tree(override_dir + dir, mc_dir + '/' + dir)
            else:
                shutil.copyfile(override_dir + dir, mc_dir + '/' + dir)
    else:
        print("This pack does not appear to include overrides")
    print("Done!")
    print()
    print()
    print()
    print("The modpack has been downloaded")
    print(ml_message)

# MOD DOWNLOADING

def get_json(session, url, logtag):
    rnd = random.random()
    time.sleep(rnd)
    gotit = False
    for tout in [3,3,4,4]:
        try:
            print(logtag + "GET (json) " + url)
            r = session.get(url, timeout=tout)
            gotit = True
            break
        except requests.Timeout as e:
            print(logtag + "timeout " + str(tout) +  "  " + url)
    if not gotit:
        try:
            print(logtag + "GET (json, long timeout) " + url)
            r = session.get(url, timeout=30)
            gotit = True
        except requests.Timeout as e:
            print(logtag + "timeout")
            import traceback
            traceback.print_exc()
            print(logtag + "Error timeout trying to access %s" % url)
            return None

    time.sleep(1-rnd)

    return json.loads(r.text)

def fetch_mod(session, f, out_dir, logtag):
    pid = f['projectID']
    fid = f['fileID']
    project_info = get_json(session, API_URL + ('/mod/%d' % pid), logtag)
    if project_info is None:
        print(logtag + "fetch failed")
        return (f, 'error')

    file_type = "mc-mods"
    info = [x for x in project_info["versions"] if x["id"] == fid]

    if len(info) != 1:
        print(logtag + "Could not find mod jar for pid:%s fid:%s, got %s results" % (pid, fid, len(info)))
        return (f, 'error')
    info = info[0]

    fn = info['name']
    dl = info['url']
    out_file = out_dir + '/' + fn

    if os.path.exists(out_file):
        if os.path.getsize(out_file) == info['size']:
            print(logtag + "%s OK" % fn)
            return (out_file, file_type)

    print(logtag + "GET (mjar) " + dl)
    status = download(dl, out_file, session=session, progress=False)
    if status != 200:
        print(logtag + "download failed (error %d)" % status)
        return (f, 'error')
    return (out_file, file_type)

async def download_mods_async(manifest, out_dir):
    with ThreadPoolExecutor(max_workers=8) as executor, \
            requests.Session() as session:
        loop = asyncio.get_event_loop()
        tasks = []
        maxn = len(manifest['files'])

        print("Downloading %s mods" % maxn)
        for n, f in enumerate(manifest['files']):
            logtag = "[" + str(n+1) + "/" + str(maxn) + "] "
            task = loop.run_in_executor(executor, fetch_mod, *(session, f, out_dir, logtag))
            tasks.append(task)

        jars = []
        manual_downloads = []
        while len(tasks) > 0:
            retry_tasks = []

            for resp in await asyncio.gather(*tasks):
                if resp[1] == 'error':
                    print("failed to fetch %s, retrying later" % resp[0])
                    retry_tasks.append(resp[0])
                elif resp[1] == 'dist-error':
                    manual_dl_url = resp[2]['links']['websiteUrl'] + '/download/' + str(resp[0]['fileID'])
                    manual_downloads.append((manual_dl_url, resp))
                    # add to jars list so that the file gets linked
                    jars.append(resp[3:])
                else:
                    jars.append(resp)

            tasks = []
            if len(retry_tasks) > 0:
                print("retrying...")
                time.sleep(2)
            for f in retry_tasks:
                tasks.append(loop.run_in_executor(executor, fetch_mod, *(session, f, out_dir)))
        return jars, manual_downloads


def download_all_mods(manifest_json, mods_dir):
    mod_jars = []
    with open(manifest_json, 'r') as f:
        manifest = json.load(f)

    loop = asyncio.get_event_loop()
    future = asyncio.ensure_future(download_mods_async(manifest, mods_dir))
    loop.run_until_complete(future)
    return future.result()

def status_bar(text, progress, bar_width=0.5, show_percent=True, borders='[]', progress_ch='#', space_ch=' '):
    ansi_el = '\x1b[K\r' # escape code to clear the rest of the line plus carriage return
    term_width = shutil.get_terminal_size().columns
    if term_width < 10:
        print(end=ansi_el)
        return
    bar_width_c = max(int(term_width * bar_width), 4)
    text_width = min(term_width - bar_width_c - 6, len(text)) # subract 4 characters for percentage and 2 spaces
    text_part = '' if (text_width == 0) else text[-text_width:]

    progress_c = int(progress * (bar_width_c - 2))
    remaining_c = bar_width_c - 2 - progress_c
    padding_c = max(0, term_width - bar_width_c - text_width - 6)

    bar = borders[0] + progress_ch * progress_c + space_ch * remaining_c + borders[1]
    pad = ' ' * padding_c
    print("%s %s%3.0f%% %s" % (text_part, pad, (progress * 100), bar), end=ansi_el)

def download(url, dest, progress=False, session=None):
    try:
        if session is not None:
            r = session.get(url, stream=True)
        else:
            r = requests.get(url, stream=True)
        size = int(r.headers['Content-Length'])

        if r.status_code != 200:
            return r.status_code

        with open(dest, 'wb') as f:
            if progress:
                n = 0
                for chunk in r.iter_content(1048576):
                    f.write(chunk)
                    n += len(chunk)
                    #status_bar(url, n / size)
            else:
                f.write(r.content)
    except requests.RequestException:
        return -1
    except OSError:
        return -2

    if progress:
        print()

    return r.status_code

# And, of course, the main:

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('zipfile')
    parser.add_argument('--mcdir', dest='mcdir')
    args = parser.parse_args(sys.argv[1:])
    main(args.zipfile, args.mcdir)
