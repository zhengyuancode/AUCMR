# JSON围栏兼容修复

已新增eval/evaluation_response_format.py，规则版本whole_json_fence_v1。仅移除占据整个响应的单个json或无语言标签代码围栏；内层JSON文本不修复、不重新序列化。仅接受LF/CRLF行边界，异常换行不做兼容转换。

接入位置：共享evaluation_runtime、L2 parse_response、L3 executor、L4 parse_response。runtime原body与完整raw_response保持原样，另给normalized_body和规范化元数据；L3 trace同时保留body与normalized_body。schema、评分、证据、预算、授权门槛保持原有规则。L4仍拒绝重复键及非有限数。

历史REJECTED_RESPONSE缓存保持失败且不改写、不重新发送。没有修改既有真实诊断脚本、配置、记录和账本；旧诊断运行仍保持STOPPED。以后新诊断需显式采用该版本规范化规则，不能重启旧运行绕过禁止重发保护。

新模块已加入三个层的implementation_manifest。此前针对旧实现的预检查/运行指纹不能当作新版本验收；旧运行不自动迁移。模型BOUND、verified、live_authorized和评价提示、指标均未修改。

## 验证

- 53项相关离线测试通过，退出0；包含新增8项测试、共享runtime、L2评价、L4运行与L3恢复测试。新增测试包含L3围栏响应完整流程及恢复不重发。
- 最后补充异常换行边界处理后，重跑新增8项离线测试，通过，退出0。
- 未运行完整260项回归。新增测试通过deny_network禁止网络，均用临时隔离数据。
- 使用已有GLM真实响应离线重放：原始JSON不可直接解析；新规则去围栏后得到正确的status=ok。见real-response-offline-replay.json。旧真实结果仍记为原始格式失败，不追溯改成成功。
- 首轮53项有1项被沙箱禁止socket构造阻止；原样在允许创建socket的环境重跑。中间一次测试期间代码被编辑，触发实现指纹变化保护；代码固定后再次原样执行53项全部通过。没有跳过测试或修改断言绕过失败。
- 修改路径的git diff --check通过。全仓库diff --check中的test.txt既有尾随空白未改动。
- 对先前10010个既有文件基线核验，变化仅为本次预期的5个已有代码文件，无删除或额外变化；另新增规范化模块及其测试。冻结批次、原始生成输出、原配置和既有评价材料保持不变，详见protection.json。

相关测试命令（仓库根目录，单行）：

`/home/dzy/miniconda3/bin/conda run --no-capture-output -n aucmr python -B -m unittest tests.test_evaluation_response_format tests.test_evaluation_runtime tests.agent_l2_independent.test_evaluation tests.agent_l4.test_runtime tests.agent_evaluation.test_recovery -q`

本轮模型调用0，未启动任何小试或正式评价。
