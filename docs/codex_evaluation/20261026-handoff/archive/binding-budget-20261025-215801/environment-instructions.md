# 环境可见性及安全输入

环境上下文给出的 shell 是 zsh。当前非登录命令子进程、登录 shell 子进程、conda run -n aucmr 子进程结果相同：智谱 false，Ark false，Qwen true。用户原交互终端没有被本轮直接观察。可以排除“仅 conda run 新丢失了两个变量”，不能证明用户此前没有设置，也不能确认唯一原因就是继承。

请在用户自己之前设置密钥的服务器 zsh 交互终端执行以下只输出存在性的单行命令；无需发任何密钥到聊天：

```sh
/home/dzy/miniconda3/bin/conda run -n aucmr python -B -c 'import os,json; print(json.dumps({k:bool(os.environ.get(k)) for k in ["AUCMR_EVAL_ZHIPU_API_KEY","AUCMR_EVAL_ARK_API_KEY","AUCMR_JUNXI_DASHSCOPE_API_KEY"]}))'
```

若此终端已存在，直接让后续执行器从该环境启动，无须重新输入。若该终端确实缺失，用下面的 zsh 隐藏输入命令；只保存本终端环境，不写文件，不把值放在命令行或历史中。set +x 用于关闭 shell 调试回显。

```sh
set +x; read -rs 'AUCMR_EVAL_ZHIPU_API_KEY?智谱密钥（隐藏输入）: ' && export AUCMR_EVAL_ZHIPU_API_KEY; print
```

```sh
set +x; read -rs 'AUCMR_EVAL_ARK_API_KEY?Ark 密钥（隐藏输入）: ' && export AUCMR_EVAL_ARK_API_KEY; print
```

export 只影响当前 shell 与之后从它启动的子进程，不会倒灌到已运行的 Codex、父进程或其他终端。若要让 Codex 执行后续任务，需要保存现有工作后，让**实际执行服务器命令的 Codex 后端/执行器**从含这些变量的父环境重新启动；仅在旁边终端 export、仅开新聊天、仅 conda activate，或从未继承这些变量的桌面启动器重启，都不能保证生效。若当前终端只用于用户手动运行配置检查，无需重启 Codex。

Codex 还可以过滤子进程环境，见 [OpenAI Docs：命令环境](https://learn.chatgpt.com/docs/config-file/config-basic)。如果重启后仍不可见，应只检查 shell_environment_policy 对这三个变量名的继承/过滤规则；不输出整个配置，不把密钥写进配置。本轮未修改该策略，也未检索 shell 历史或密钥文件。实际启动方式未确认，所以不提供可能启动错误实例的命令。重启后只重复存在性检查即可。
