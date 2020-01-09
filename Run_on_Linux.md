# 在 Linux 上运行桌面助手

## 1. 准备

首先，在系统上先安装好 Python2 和 PyQt4。由于现在没法通过 pip 直接安装 PyQt4 了，所以建议使用 Miniconda 来安装 PyQt4。

Miniconda 的介绍和下载可以查看 [Miniconda](https://conda.io/miniconda.html)。也可以使用 [清华大学开源软件镜像站](https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/) 提供的国内镜像。

推荐使用清华大学开源软件镜像站提供的镜像，并在安装结束后修改一下 conda 和 pip 的软件源。

```shell
$ wget https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ./miniconda3.sh
...
$ bash ./miniconda3.sh -b -p $HOME/miniconda
...
$ export PATH="$HOME/miniconda/bin:$PATH"
$ conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/free/
$ conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main/
$ conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/
$ conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/msys2/
$ conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/bioconda/
$ conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/menpo/
$ conda config --set show_channel_urls yes
```

pip 软件源修改：修改 ~/.config/pip/pip.conf
```
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
```

## 2. 虚拟环境

```shell
$ conda create -n dev python=2.7 -y
...
$ source activate dev
(dev) $ which python
/home/user/miniconda/envs/dev/bin/python
```

## 3. 安装依赖

```shell
(dev) $ conda install pyqt=4 -y
...
(dev) $ cd /home/user/edo_client_src
(dev) $ pip install -r requirements.txt && python setup.py install
...
(dev) $ cd /home/user/assistent_pyc
(dev) $ pip install -r requirements.txt
...
(dev) $ pybabel compile -d translations
...
```

## 4. 启动桌面助手

```shell
(dev) $ python main.pyc
```
