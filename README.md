# 天津九年级化学题库

本地网页题库，按人教版九年级化学的单元和课题浏览、检索和组卷。在本机运行，无需登录。

## 如何运行

```bash
python3 -m venv venv
source venv/bin/activate
pip install python-docx lxml
python app.py serve
```

浏览器打开 http://127.0.0.1:8765 。

变式生成会从环境变量读取 `GLM_API_KEY` 和 `DEEPSEEK_API_KEY`。没有这两项时，浏览和组卷仍可使用。不要把密钥写进代码或提交到仓库。

## 不在本仓库中

题目数据库（`bank.sqlite`）和题目图片（`media/`）没有放进本仓库，需要在本机自行准备后再启动。
