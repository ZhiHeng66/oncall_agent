# 实验 05：为高风险工具增加人工审批

## 场景

模拟 `restart_service(service_name)`。实验中不要连接真实生产服务，只修改本地测试状态。

## 流程

1. Tool Registry 将该工具标为 high risk、write、idempotent=false。
2. Executor 产生调用后，Policy Engine 阻止执行并创建 pending action。
3. Graph 持久化状态并中断。
4. 审批 API 返回工具名、参数、风险、依据和过期时间。
5. 用户批准后恢复；恢复前再次检查 owner、权限和 action hash。
6. 生成幂等键并执行一次。
7. 拒绝、过期、参数被篡改都不得执行。

## 验收

- 未批准调用数为 0。
- 重复点击批准只执行一次。
- 审批后重启服务仍能恢复。
- 审计记录能说明谁在何时批准了什么。

