# macOS 版本使用说明

## 安装

1. 下载 `夏令营日程助手-macOS.dmg`。
2. 双击打开 DMG。
3. 将 `夏令营日程助手.app` 拖入 `Applications`。
4. 从 `Applications` 中打开软件。

如果提示“无法验证开发者”，请在 Finder 中右键点击 `夏令营日程助手.app`，选择“打开”。

## 激活

Windows 和 macOS 使用同一种激活码。

macOS 第一次打开软件时会提示输入激活码。激活成功后，授权信息会写入 macOS Keychain，并与当前 macOS 用户和应用路径做轻绑定。

软件每次启动都需要联网同步时间。无法联网时会提示：

`软件功能需要联网同步时间以使用网页读取与AI 服务，请联网后重新打开`

## 数据位置

macOS 用户数据默认保存在：

`~/Library/Application Support/SummerCampPlanner`

包括日程数据库、AI 设置和个人备忘录。

## 卸载

删除 `Applications` 中的 `夏令营日程助手.app` 即可卸载主程序。

如需清空用户数据，可删除：

`~/Library/Application Support/SummerCampPlanner`

如需清理授权记录，可打开“钥匙串访问”，搜索 `summer-camp-planner` 后删除对应项目。
