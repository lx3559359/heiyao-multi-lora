# 黑曜资源 · 个人多 LoRA 加载器

由黑曜资源提供。 [黑曜资源官网](https://openheiyao.top/)

## 使用

在 ComfyUI Manager 搜索 `heiyao-multi-lora`，安装后重启 ComfyUI。然后在画布添加「个人多 LoRA 加载器」。通过「新增 LoRA」和每行右侧的删除按钮按需调整槽位。勾选「带词」后，节点在场景词前插入该槽位选用的触发词。

- 「简短」为默认档，角色 LoRA 带入角色识别词和画风。
- 「头部」带入官网或本地预设的完整头部特征。没有头部预设的权重沿用简短档。
- 「触发词」编辑当前档，可以仅保存到工作流，或保存为此 LoRA 的本机默认值。既有工作流里的自定义触发词保留在简短档。

将 `model` 输出接到采样器，将 `positive_prompt` 接到 `CLIPTextEncode` 的 `text` 输入。需要对 CLIP 应用 LoRA 时，把上游 CLIP 接入加载器，使用加载器的 `clip` 输出编码。节点类型 `PersonalLoRALoader` 支持既有工作流。

在本机地址打开 ComfyUI 时，可从节点顶部打开「官网导入」。搜索角色名或粘贴官网 LoRA 详情链接。插件先核验主 LoRA 目录中的同版文件，再按官网会员权限下载缺失权重。下载完成并通过 SHA-256 校验后保存到该目录的 `黑曜官网` 子目录。

## 更新与数据

自定义触发词和官网设备身份写在 ComfyUI 用户目录下的 `__personal_lora` 中，独立于节点包目录。旧版配置仍在原插件目录时，新版首次加载会迁移现有文件。已在用户目录保存的值优先。

在 Manager 中对 `heiyao-multi-lora` 检查更新，安装所需版本后重启 ComfyUI。

## 发布

需要在线分发的功能更新通过本地验证后，递增 `pyproject.toml` 的版本号并提交到 `main`。在 GitHub Actions 运行 `Publish to Comfy Registry` 发布相同版本。该工作流使用仓库的 `REGISTRY_ACCESS_TOKEN` Secret。Registry 版本激活后，在 Manager 检查更新、安装并重启验证。 [Comfy Registry 发布指南](https://docs.comfy.org/registry/publishing)
