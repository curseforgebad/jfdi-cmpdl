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
import tempfile
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile


API_URL = 'https://api.modpacks.ch/public'
WORKERS = 6
REQUESTS_PER_SEC = 4
SLEEP_SECONDS = WORKERS / REQUESTS_PER_SEC


def main(zipfile, *, packdata_dir, mc_dir=None):
    # Extract pack
    packname = os.path.splitext(zipfile)[0]
    packname = os.path.basename(packname)

    if not mc_dir:
        if not os.path.isdir('packs/'):
            os.mkdir('packs/')
        mc_dir = 'packs/' + packname
    # Generate minecraft environment
    print("Output directory is '%s'" % mc_dir)
    if os.path.isdir(mc_dir):
        if os.listdir(mc_dir):
            print("Error: Output directory already exists and is not empty")
            return
        else:
            print("Output directory exists (and is empty)")
    else:
        print("Creating output directory")
        os.mkdir(mc_dir)


    print("Extracting %s" % zipfile)
    with ZipFile(zipfile, 'r') as zip:
        zip.extractall(packdata_dir)

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
        print("Copying mods")
        os.mkdir(mc_dir + '/mods')
        os.mkdir(mc_dir + '/resources')

        for mod in mods:
            jar = mod[0]
            type = mod[1]
            if type == 'mc-mods':
                modfile = mc_dir + '/mods/' + os.path.basename(jar)
                if not os.path.exists(modfile):
                    cp_safe(os.path.abspath(jar), modfile)
            elif type == 'texture-packs':
                print("Extracting texture pack %s" % jar)
                with tempfile.TemporaryDirectory() as texpack_dir:
                    with ZipFile(jar, 'r') as zip:
                        zip.extractall(texpack_dir)
                    for dir in os.listdir(texpack_dir + '/assets'):
                        f = texpack_dir + '/assets/' + dir
                        cp_safe(f, mc_dir + '/resources/' + dir)
            else:
                print("Unknown file type %s" % type)
                sys.exit(1)

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
    rnd = random.random() * SLEEP_SECONDS
    time.sleep(rnd)
    gotit = False
    for tout in [3,5,10,20,30]:
        try:
            print(logtag + "GET (json) " + url)
            r = session.get(url, timeout=tout)
            gotit = True
            break
        except requests.Timeout as e:
            print(logtag + "timeout %02d %s" % (tout, url))
    if not gotit:
        try:
            print(logtag + "GET (json, long timeout) " + url)
            r = session.get(url, timeout=120)
            gotit = True
        except requests.Timeout as e:
            print(logtag + "timeout")
            import traceback
            traceback.print_exc()
            print(logtag + "Error timeout trying to access %s" % url)
            return None

    time.sleep(SLEEP_SECONDS - rnd)

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
    with ThreadPoolExecutor(max_workers=WORKERS) as executor, \
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

def cp_safe(src, dst):
    if os.path.exists(dst):
        raise FileExistsError("Cannot copy '%s' -> '%s' because the destination already exists" % (src, dst))
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copyfile(src, dst)

# And, of course, the main:

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('zipfile')
    parser.add_argument('--outdir', dest='outdir')
    args = parser.parse_args(sys.argv[1:])
    with tempfile.TemporaryDirectory() as packdata_dir:
        main(args.zipfile, packdata_dir=packdata_dir, mc_dir=args.outdir)
