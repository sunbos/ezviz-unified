# H014：完整JSON请求体契约与离线评测

OTAP写接口的参数表把整体JSON称为Body；真实请求是裸JSON对象。当前评测器仅支持已命名body字段，不能用虚构的Body包装或写死timeZone字段迁就现有评测。

增加可选contract.request_body={"type":"object","required":true,"evidence":"..."}，只用于body_encoding=json、token_location=header/query/none的完整JSON对象契约；不与任何location=body参数混用。暂不支持JSON token与整体body共用，显式拒绝而非静默覆盖。旧契约无该字段时行为完全不变。

对应case.json_body为独立的完整JSON对象，fields只记录命名header/query字段。整体body契约必须有json_body且为dict；其它契约禁止json_body。拒绝非字符串键、非有限值、保留accessToken顶层键、误用namedbody字段、缺必填header/未知fields。不把body内部产品属性当作未声明顶层HTTP参数。

保持标准token注入、Content-Type、query/header/form检查。传输层对JSON对象做逐类型递归比较（bool/int/float/string/null及array顺序区分，object顺序不相关）；不允许类型相等假象掩盖篡改。既有类型严格工具能复用则复用。

独立回归应覆盖合法嵌套对象/数组/空对象/null值、头参数与token、类型篡改、未知/缺失字段、非法schema组合；旧的全部177契约1448次离线执行必须不退化。新增合成wholebody契约仅作工具单测，不加入当前真实支持统计。SDK、真实registry/cases与账号均不改动。
