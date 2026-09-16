---
name: "repo-hygiene"
description: "Use when cleaning a repo or prepping it to publish."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\software-development\\repo-hygiene\\SKILL.md"
---
# 仓库卫生：清理无关目录 / 临时文件 / 发布前整理

触发：用户说「跟项目无关的文件夹和临时文件清理下」「准备开源」「仓库怎么这么多垃圾」。

核心原则：**只删白名单，数据与环境绝不碰，先算再删，删完验证。** 删除不可逆，一次误删比十次没清干净贵得多。

## 流程（每步都要做）

1. **盘点**。别用 `du -sh *`——仓库里只要有一个几千文件的快照/依赖目录，它就会跑到超时。用 `os.walk` 边遍历边累计（文件数 + 字节），或先 `ls -1F` 看顶层、再逐个 `ls -1 <dir> | wc -l` 估规模。
2. **分类**：① 无关第三方快照/抽取物 ② 构建产物 ③ 探针脚本与草稿 ④ 缓存（`__pycache__`/`*-cache`） ⑤ 运行期状态（pid/log/snapshot） ⑥ 日志。
3. **删前查引用**：`grep -rl "<目录名>" --include=*.py --include=*.ts --include=*.tsx --include=*.md .`。有源码引用就先别删，或先把引用改成优雅降级。
4. **用脚本删，不要手敲 `rm -rf`**：把候选列表写进一个 `.py`（放仓库**外面**的 temp 目录，否则它自己也是垃圾），脚本先打印「每个目标的大小 + 文件数」计划，再执行，最后报告释放空间/跳过项/失败原因。候选列表写死在脚本里 = 天然白名单。
5. **加固 `.gitignore`**（成组写，别散落）：否则明天原样长回来。
6. **验证**：`python -c "import <主包>, api.app"`（或等价的启动路径）+ 跑一个快测试子集。清理不属于“改代码”，但必须要能证明确实没删坏东西。

## 绝不动（数据/环境/真源）

`.git`、虚拟环境（`.venv`/`venv`）、`node_modules`、数据目录（库文件、配置、`settings.json`）、工作目录（workspace/项目产物）、`vendor/`、源码目录与 `tests/`。

拿不准就只报告不删，列表交给用户勾选：常见“看着像垃圾但不该我定”的有——**别的工具的配置目录**（IDE/agent 配置）、数据目录、仓库内的**内部过程文档**（轮次报告、历史 review 报告）。

## 技术坑（Windows + git-bash 实测）

- **日志要截断不要删**：服务还在跑并持有日志句柄，`unlink` 会失败；`open(path, "w")` 截断即使被占用也能成功。
- **临时目录里可能嵌着只读的 `.git/objects`**（演示/子仓库建的）→ `shutil.rmtree(path, onerror=handler)`，handler 里 `os.chmod(p, stat.S_IWRITE | stat.S_IREAD)` 后重试；直接 rmtree 会 WinError 5 拒绝访问并删一半。
- **目录不存在时的二次删除不算错**：白名单目录与缓存扫描可能重叠，跳过即可，别把它当失败报警。
- **工具坑**：嵌套引号/`$()`/`for` 循环里再套管道的复杂内联命令会被命令解析器直接拦掉 → 写 `.py`/`.sh` 脚本到仓库外再执行。`search_files` 的 pattern 里带 `\(` 会被弄坏（unclosed group）→ 用不带括号的裸标识符先搜。

## 删完必须告诉用户的两件事

1. 被删的多为**已跟踪**文件时，`git status` 会出现成百上千行 `D`；**提交前 `git checkout .` 会把它们全复活**。要真正落地就得提交（或一次性 `checkout --orphan` 全新初始提交）。
2. **`.git` 不会因此变小**：工作树省下的空间 ≠ 仓库瘦身，历史里的 blob 还在。真瘦身要 `git filter-repo --path <垃圾> --invert-paths` + `git gc --aggressive --prune=now`——这会**重写历史**，属于不可逆操作，**必须先拿到用户明确同意再动手**，并提醒协作者需要重新 clone。

## 发布前的通用动作（清完垃圾还没完）

1. **清掉本机痕迹与占位符**：绝对路径、用户名、`OWNER/REPO` 类占位符。写一个替换脚本一次改完（README 的 badge 与 clone URL、`pyproject` 的 urls、issue/PR 模板、SECURITY/CODE_OF_CONDUCT 的联系方式）。**替换脚本必须排除自己**——它自己的正则里就含那些字面量，被替换后既不再匹配也无法再跑；改完用 `grep -rn "OWNER/REPO"`（排除脚本自身）确认残留为 0。查泄漏用 `git grep -lI "<用户名>\|<盘符路径>"`：**用 `git grep`，不要裸 `grep`**，否则扫进 `.venv`/`node_modules` 全是假阳性。
2. **许可证要写进产物**：`LICENSE` 放逐字全文（从 gnu.org / choosealicense 取，别手打），`pyproject` 用 SPDX 表达式 + `license-files`。**文件必须以规范标题行开头**：把自己项目的声明写在最前面，GitHub 的识别器就认不出来（仓库页显示 `NOASSERTION`、没有 license 标识，而这是别人筛项目会看的字段）✗；项目版权声明放到 README 的 License 段，改完用 API 确认 `license.spdx_id`（如 `AGPL-3.0`），不要只看文件内容 ✓。换许可证时改掉**所有**旧表述（badge、许可证段、CONTRIBUTING、NOTICE），然后**读构建产物的元数据验证**（wheel 的 `METADATA` 里 `License-Expression` / `License-File`、`dist-info/licenses/` 下有没有文件），不要只读配置就说改好了。vendor 进来的第三方代码保留自己的许可证，在 `NOTICE` 里写明（MIT 与 (A)GPL-3.0 单向兼容）。
3. **改了配置就要构建产物并打开看**：`pip wheel` / `npm run build` 之后 inspect 内容（`zipfile.namelist()`、`ls dist/`）。配置看着对、实际什么都没打进去是常见结果。两种边界都测：文件**存在**时进不进得去、文件**缺失**时是优雅缺省还是硬失败（后者会炸全新 clone 的 `pip install -e .`）。把非包文件（前端构建产物、迁移文件、模板）打进 wheel 的具体写法、实测对比表与验证脚本见 [`references/packaging-artifacts.md`](references/packaging-artifacts.md)。
4. **测试套件不得改动仓库或开发者的数据目录**：发布前跑一次全量，用 `BEFORE=$(git rev-parse HEAD)` / `AFTER` 对比 + `git status --porcelain` 必须干净。跑真实循环 / 写库 / 检查点的用例最容易静默 commit 或改文件（默认 workspace 往往就是进程 CWD，即仓库根）。发现后两条一起上：给测试显式临时目录，再加一个 env 级 kill-switch 并在 conftest 里全局置上、让故意测该路径的用例局部豁免。
   **数据目录同样要隔离**：默认数据目录就在仓库里（`<repo>/data`），走真实持久化层的用例会往开发者的库里写测试项目——它们随后会出现在 README 截图的侧边栏里 ✗。在 conftest 的 `pytest_configure` 里把数据目录 env（本项目是 `KAIROS_DATA_DIR`）指向 `mkdtemp()` ✓；注意实现往往是**在 import 时**读取它，而 hook 早于收集 ✓。守卫要断言**应用真正解析到的目录**，只断言环境变量不够 ✗（删掉变量 / 晚于 import 设置时，环境变量看着仍然对）。证明码：
   ```bash
   B=$(md5sum data/<db> | cut -d' ' -f1); python -m pytest tests -q
   [ "$B" = "$(md5sum data/<db> | cut -d' ' -f1)" ] && echo "真实库未被写入 ✓"
   ```
5. **内部过程文档先问再动**：轮次报告 / 历史 review / 调研草稿这类，用户的取舍是「留在 `docs/internal/` 并配一个说明索引说明它们描述的是中间状态」，而不是删掉——先问，别自作主张。
6. **README 要能自证**：相对链接逐个可解析、示例输出是真跑出来的、截图来自真实界面（不是占位图）。**截图要从干净数据重拍**（绝不截开发机数据库 ✗——里面有测试夹具、手改过的示例项目名、本机路径），headless 渲染与 DOM 验证的具体做法见 [`references/readme-screenshots.md`](references/readme-screenshots.md)。
7. **密钥审计必须扫全历史**：`git grep` 只能证明「现在没泄漏」，而实测泄漏的都出自「曾经提交过、后来删掉」的文件（根目录 `settings.json`、早期调试脚本、已删的 `docs/*`）。逐提交扫（每个 sha 跑一次 `git grep`，别用 Python 逐 blob 读管道，Windows 上慢一个数量级）：
   ```bash
   for c in $(git rev-list --all); do git grep -hIE "<密钥正则>" "$c"; done | sort -u
   ```
   占位符（含 `here/xxx/your/example/fake/dummy/test` 等词）与真值要分开统计；「这把还在用吗」用**哈希比对**回答（`sha256[:12]` 与配置里当前值对比，**永不打印密钥本身**）。抹除用 `git filter-repo --force --replace-text <file>`，文件内容 `literal:<key>==>REDACTED`；**该文件自身含明文**，放在仓库外、用完立即 `rm`。同一办法可抹机器用户名（`literal:leohu==>user`，当前文件与全历史一起改）。历史里出现过的密钥仍建议轮换——抹除 ≠ 作废；重写前建的 bundle 含明文，删掉、重建一份干净的。
8. **发布后必须在干净 clone 里跑一遍 CI 的命令**（`git clone` → 在 clone 里跑 `merge_i18n --strict` 之类的门禁脚本、demo、`pytest`）。工作树会掩盖四类缺陷：① `.gitignore` 未锚定的规则吞掉源码（裸 `settings.json` 连带匹配 `web/src/i18n/parts/settings.json` → 几百个源键从未入库，每个干净 clone 的严格校验全报 stale；规则一律写锚定形式）② vendored 第三方内容被提交成 gitlink(160000)（clone 只得到空目录；**两条都要看**：`git ls-files -s | awk '$1==160000'` **和** `git ls-tree -r HEAD | awk '$1==160000'`；每次 `filter-repo` 重写后重新确认；修法 `git rm --cached <p>` + `git add -f <p>`）③ 未声明依赖（本地 venv 里碰巧装过 → CI 里直接 collection error；比对顶层 import 与 pyproject 的 dependencies/extras，名字要先 `-`→`_` 归一化，否则 `prometheus-client` vs `prometheus_client` 全是假阳性）④ 工具在 CI 侧缺 `node_modules`（例如门禁脚本从 `web/node_modules` 解析 typescript → 该 job 必须先 `npm ci`）。
9. **CI 时长先量再配，别把“被杀”当成“很慢”**：按本地耗时给分片定权重前，先拿 CI 自己的每种用例耗时（`--durations=0`）——本项目实测一个“跑 40 分钟”的分片，里面测试合计只有 **44 秒**，真因是**被 SIGKILL**（退出码 137，不是超时）✗；而 137 **不等于 OOM**：死时内存还剩几 GB，真因是 hook 超时 `killpg` 打到了自己的进程组（自杀式 SIGKILL）。分片 / 诊断 / 内存定位的完整做法（含退出码语义表、被杀与取消的 job 为什么没有日志、`scripts/ci_shard.sh` 逐文件模式）见 [`references/ci-failure-triage.md`](references/ci-failure-triage.md)。运行结论是 `cancelled` 时先看 job 的步骤时间线：**`cancelled` 不等于测试失败**——私有仓库的 Actions 免费额度耗尽会在运行中取消 job（public 仓库分钟数免费）。

发布流程本身（装 `gh`、设备码登录、受限网络下改用 SSH-over-443、建仓/默认分支/提交身份、干净 clone 验收脚本）见 [`references/publish-and-verify-on-github.md`](references/publish-and-verify-on-github.md)。

10. **仓库元数据写前先读**：`gh repo view --json description,repositoryTopics,homepageUrl` 看一遍再改——描述/topics 往往是用户定过一次的对外定位语（本项目那句是 "you don't ship what the agent didn't pass"），直接 `gh repo edit --description …` 会把它静默覆盖 ✗；写错了立刻改回原文 ✓。

## 项目专属清单放哪

白名单的具体目录名、该保留哪些数据目录、有哪些内部文档——这些随仓库变化，写进该仓库自己的 ops/运维 skill（例如 `kairos-code-ops`），本技能只保留通用流程与坑。
