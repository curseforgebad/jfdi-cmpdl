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
import traceback
import hashlib
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

    ml_message = 'You then need to install: '
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

    print("Copying mods")
    os.mkdir(mc_dir + '/mods')
    os.mkdir(mc_dir + '/resources')

    # TODO detect texture packs
    #for mod in mods:
    #    jar = mod[0]
    #    type = mod[1]
    #    if type == 'mc-mods':
    #        modfile = mc_dir + '/mods/' + os.path.basename(jar)
    #        if not os.path.exists(modfile):
    #            cp_safe(os.path.abspath(jar), modfile)
    #    elif type == 'texture-packs':
    #        print("Extracting texture pack %s" % jar)
    #        with tempfile.TemporaryDirectory() as texpack_dir:
    #            with ZipFile(jar, 'r') as zip:
    #                zip.extractall(texpack_dir)
    #            for dir in os.listdir(texpack_dir + '/assets'):
    #                f = texpack_dir + '/assets/' + dir
    #                cp_safe(f, mc_dir + '/resources/' + dir)
    #    else:
    #        print("Unknown file type %s" % type)
    #        sys.exit(1)

    # Copy overrides
    override_dir = packdata_dir + '/overrides/'
    if os.path.exists(override_dir):
        print("Copying overrides")
        for dir in os.listdir(override_dir):
            print(dir + "...")
            cp_safe(override_dir + dir, mc_dir + '/' + dir)

    else:
        print("Copying overrides [nothing to do]")

    print("Done!\n\n\n\nThe modpack has been downloaded to: " + mc_dir)
    print(ml_message)
    if len(manual_downloads) > 0:
        msg=""
        msg+="====MANUAL DOWNLOAD REQUIRED====\n"
        msg+="The following mods failed to download\n"
        msg+="Please download them manually and place them in " + mc_dir + "/mods\n"
        for url, resp in manual_downloads:
            msg+="* %s\n" % url
        print(msg[:-1])
        with open(mc_dir + '/MANUAL-DOWNLOAD-README.txt', 'w') as f:
            f.write(msg)



# MOD DOWNLOADING

def get_json(session, url, logtag):
    gotit = False
    print(logtag + "GET (json) " + url)
    for tout in [4,5,10,20,30]:
        try:
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
            traceback.print_exc()
            print(logtag + "Error timeout trying to access %s" % url)
            return None

    return json.loads(r.text)

def fetch_mod(session, f, out_dir, logtag, attempt):
    rnd = random.random() * SLEEP_SECONDS
    time.sleep(rnd)
    try:
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
            return (f, 'dist-error' if attempt == "retry" else 'error', project_info)
        info = info[0]

        fn = info['name']
        dl = info['url']
        sha1_expected = info['sha1'].lower()
        out_file = out_dir + '/' + fn

        if os.path.exists(out_file):
            if os.path.getsize(out_file) == info['size'] and sha1_expected == sha1(out_file):
                print(logtag + "%s OK cached" % fn)
                return (out_file, file_type)

        status = download(dl, out_file, session=session, progress=False)
        time.sleep(SLEEP_SECONDS - rnd)
        if sha1_expected != sha1(out_file):
            print(logtag + "download failed (SHA1 mismatch!)" % status)
            return (f, 'error')
        if status != 200:
            print(logtag + "download failed (error %d)" % status)
            return (f, 'error')
        print(logtag + "%s OK downloaded" % fn)
        return (out_file, file_type)
    except:
        print(logtag + "download failed (exception)")
        traceback.print_exc()
        return (f, 'dist-error' if attempt == "retry" else 'error', project_info)

async def download_mods_async(manifest, out_dir):
    with ThreadPoolExecutor(max_workers=WORKERS) as executor, \
            requests.Session() as session:
        loop = asyncio.get_event_loop()
        tasks = []
        maxn = len(manifest['files'])

        print("Downloading %s mods" % maxn)
        for n, f in enumerate(manifest['files']):
            logtag = "[" + str(n+1) + "/" + str(maxn) + "] "
            task = loop.run_in_executor(executor, fetch_mod, *(session, f, out_dir, logtag, "first attempt"))
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
                    print(resp[2])
                    manual_dl_url = resp[2]['links'][0]['link'] + '/download/' + str(resp[0]['fileID'])
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
                tasks.append(loop.run_in_executor(executor, fetch_mod, *(session, f, out_dir, logtag, "retry")))
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
                    status_bar(url, n / size)
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

def sha1(src):
    h = hashlib.sha1()
    with open(src, 'rb') as f:
        while True:
            data = f.read(4096)
            if not data:
                break
            h.update(data)
    return h.hexdigest()

# And, of course, the main:

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('zipfile')
    parser.add_argument('--outdir', dest='outdir')
    args = parser.parse_args(sys.argv[1:])
    with tempfile.TemporaryDirectory() as packdata_dir:
        main(args.zipfile, packdata_dir=packdata_dir, mc_dir=args.outdir)
