import typer
import requests
from typing_extensions import Annotated
import os
import re
import logging
import fnmatch
import argparse
import urllib.parse
from tqdm import tqdm

# 创建typer项目
app = typer.Typer()
# 创建一个requests会话对象，用于发送网络请求
sess = requests.Session()
# 设置日志记录的基本配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 解析命令行参数
def parse_args():
    # 创建ArgumentParser对象
    args = argparse.ArgumentParser()
    # 添加命令行参数：-l/--link，必须提供，用于指定清华大学云盘的分享链接
    args.add_argument('-l', '--link', type=str, required=True, help='Share link of Tsinghua Cloud')
    # 添加命令行参数：-s/--save_dir，非必须，用于指定文件保存的目录，默认为桌面
    args.add_argument('-s', '--save_dir', type=str, default=None, help='Path to save the files. Default: Desktop')
    # 添加命令行参数：-f/--file，非必须，用于指定文件路径的正则表达式，用于筛选文件
    args.add_argument('-f', '--file', type=str, default=None, help='Regex to match the file path')
    return args.parse_args()

# 从分享链接中提取关键字
def get_share_key(url: str) -> str:
    prefix = 'https://cloud.tsinghua.edu.cn/d/' 
    # 检查分享链接是否以指定的前缀开头
    if not url.startswith(prefix):
        raise ValueError('Share link of Tsinghua Cloud should start with {}'.format(prefix))
    # 提取分享关键字
    share_key = url[len(prefix):].replace('/', '')
    logging.info('Share key: {}'.format(share_key))
    return share_key

# 获取分享链接的根目录名称
def get_root_dir(share_key: str) -> str:
    global sess
    pattern = '<meta property="og:title" content="(.*)" />'
    # 发送请求获取根目录名称
    r = sess.get(f"https://cloud.tsinghua.edu.cn/d/{share_key}/") 
    root_dir = re.findall(pattern, r.text)
    # 确保找到了根目录名称
    assert root_dir is not None, "Couldn't find title of the share link."
    logging.info("Root directory name: {}".format(root_dir[0]))
    return root_dir[0]

# 验证分享链接是否需要密码，并验证密码
def verify_password(share_key: str) -> None:
    global sess
    r = sess.get(f"https://cloud.tsinghua.edu.cn/d/{share_key}/") 
    pattern = '<input type="hidden" name="csrfmiddlewaretoken" value="(.*)">'
    csrfmiddlewaretoken = re.findall(pattern, r.text)
    if csrfmiddlewaretoken:
        pwd = input("Please enter the password: ")
        csrfmiddlewaretoken = csrfmiddlewaretoken[0]
        data = {
            "csrfmiddlewaretoken": csrfmiddlewaretoken,
            "token": share_key,
            "password": pwd
        }
        # 发送带有密码的请求
        r = sess.post(f"https://cloud.tsinghua.edu.cn/d/{share_key}/",  data=data,
                    headers={"Referer": f"https://cloud.tsinghua.edu.cn/d/{share_key}/"}) 
        if "Please enter a correct password" in r.text:
            raise ValueError("Wrong password.")

# 判断文件路径是否匹配用户提供的正则表达式
def is_match(file_path: str, pattern: str) -> bool:
    file_path = file_path[1:] # 移除路径的第一个'/'
    return pattern is None or fnmatch.fnmatch(file_path, pattern)

# 递归搜索文件
def dfs_search_files(share_key: str, 
                     path: str = "/", 
                     pattern: str = None) -> list:
    global sess
    filelist = []
    encoded_path = urllib.parse.quote(path)
    # 发送请求获取文件列表
    r = sess.get(f'https://cloud.tsinghua.edu.cn/api/v2.1/share-links/{share_key}/dirents/?path={encoded_path}') 
    objects = r.json()['dirent_list']
    for obj in objects:
        if obj["is_dir"]:
            filelist.extend(
                dfs_search_files(share_key, obj['folder_path'], pattern))
        elif is_match(obj["file_path"], pattern):
            filelist.append(obj)
    return filelist

# 单个文件的下载函数
def download_single_file(url: str, fname: str, pbar: tqdm):
    global sess
    resp = sess.get(url, stream=True)
    with open(fname, 'wb') as file:
        for data in resp.iter_content(chunk_size=1024):
            size = file.write(data)
            pbar.update(size)

# 打印文件列表
def print_filelist(filelist):
    print("=" * 100)
    print("Last Modified Time".ljust(25), " ", "File Size".rjust(10), " ", "File Path")
    print("-" * 100)
    for i, file in enumerate(filelist, 1):
        print(file["last_modified"], " ", str(file["size"]).rjust(10), " ", file["file_path"])
        if i == 100:
            print("... %d more files" % (len(filelist) - 100))
            break
    print("-" * 100)

# 下载文件的函数
def download(share_key: str, filelist: list, save_dir: str) -> None:
    if os.path.exists(save_dir):
        logging.warning("Save directory already exists. Files will be overwritten.")
    total_size = sum([file["size"] for file in filelist])
    pbar = tqdm(total=total_size, ncols=120, unit='iB', unit_scale=True, unit_divisor=1024)
    for i, file in enumerate(filelist):
        file_url = 'https://cloud.tsinghua.edu.cn/d/{}/files/?p={}&dl=1'.format(share_key,  file["file_path"])
        save_path = os.path.join(save_dir, file["file_path"][1:])
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        try:
            pbar.set_description("[{}/{}]".format(i + 1, len(filelist)))
            download_single_file(file_url, save_path, pbar)
        except Exception as e:
            logging.error("Error happened when downloading file: {}".format(save_path))
            logging.error(e)
    pbar.close()
    logging.info("Download finished.")



@app.command()
def main(
    link: str,
    output: str = typer.Option(None, "--output", "-o", help="output path to save the files. Default: ~/Downloads"),
    match_pattern: str = typer.Option("*.*", "--pattern", "-p", help="Regex pattern to match targeted file path. Eg: '*.pptx?'")
):
    """
    Download files from THU cloud directory and save them to a specified path.
    """
    # if link:
    #     typer.echo(f"Link: {link}")
    # if output:
    #     typer.echo(f"Save path: {output}")
    # if match_pattern:
    #     typer.echo(f"File filter: {match_pattern}")

    share_key = get_share_key(link)
    verify_password(share_key)
    
    # 搜索文件
    logging.info("Searching for files to be downloaded, Wait a moment...")
    filelist = dfs_search_files(share_key, pattern=match_pattern)
    filelist.sort(key=lambda x: x["file_path"])
    if not filelist:
        logging.info("No file found.")
        return

    print_filelist(filelist)
    total_size = sum([file["size"] for file in filelist]) / 1024 / 1024 # MB
    logging.info(f"# Files: {len(filelist)}. Total size: {total_size: .1f} MB.")
    key = input("Start downloading? [y/n]")
    if key != 'y':
        return
    

    # 默认保存到桌面
    save_dir = output
    if save_dir is None:
        save_dir = os.path.join(os.path.expanduser("~"), 'Downloads')
        assert os.path.exists(save_dir), "Downloads folder not found."
    root_dir = get_root_dir(share_key)
    print(root_dir)
    save_dir = os.path.join(save_dir, root_dir)
    
    download(share_key, filelist, save_dir)
        

if __name__ == "__main__":
    app()