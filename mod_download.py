#!/usr/bin/env python3
import os
import sys
import requests
import json
import asyncio
import time
import random
from util import download
from concurrent.futures import ThreadPoolExecutor

api_url = 'https://api.modpacks.ch/public'

def get_json(session, url):
    rnd = random.random()
    time.sleep(rnd)
    gotit = False
    for tout in [3,3,4,4]:
        try:
            print("GET (json) " + url)
            r = session.get(url, timeout=tout)
            gotit = True
            break
        except requests.Timeout as e:
            print("timeout " + str(tout) +  "  " + url)
    if not gotit:
        try:
            print("GET (json, long timeout) " + url)
            r = session.get(url, timeout=30)
            gotit = True
        except requests.Timeout as e:
            print("timeout")
            import traceback
            traceback.print_exc()
            print("Error timeout trying to access %s" % url)
            return None

    time.sleep(1-rnd)

    return json.loads(r.text)

def fetch_mod(session, f, out_dir):
    pid = f['projectID']
    fid = f['fileID']
    project_info = get_json(session, api_url + ('/mod/%d' % pid))
    if project_info is None:
        print("fetch failed")
        return (f, 'error')

    file_type = "mc-mods"
    info = [x for x in project_info["versions"] if x["id"] == fid]

    if len(info) != 1:
        print("Could not find mod jar for pid:%s fid:%s, got %s results" % (pid, fid, len(info)))
        return (f, 'error')
    info = info[0]

    fn = info['name']
    dl = info['url']
    out_file = out_dir + '/' + fn

    if os.path.exists(out_file):
        if os.path.getsize(out_file) == info['size']:
            print("%s OK" % fn)
            return (out_file, file_type)
    
    print("GET (mjar) " + dl)
    status = download(dl, out_file, session=session, progress=False)
    if status != 200:
        print("download failed (error %d)" % status)
        return (f, 'error')
    return (out_file, file_type)

async def download_mods_async(manifest, out_dir):
    with ThreadPoolExecutor(max_workers=8) as executor, \
            requests.Session() as session:
        loop = asyncio.get_event_loop()
        tasks = []
        for f in manifest['files']:
            task = loop.run_in_executor(executor, fetch_mod, *(session, f, out_dir))
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


def main(manifest_json, mods_dir):
    mod_jars = []
    with open(manifest_json, 'r') as f:
        manifest = json.load(f)

    print("Downloading mods")

    loop = asyncio.get_event_loop()
    future = asyncio.ensure_future(download_mods_async(manifest, mods_dir))
    loop.run_until_complete(future)
    return future.result()

if __name__ == "__main__":
    print(main(sys.argv[1], sys.argv[2]))
