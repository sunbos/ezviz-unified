# 统一存储契约与边界

本批依据2026-11-14归档复核统一存储引擎的七个API，同步/异步入口为`client.storage`，文件查询使用`StorageFileQuery`。已完成离线报文验收，没有真实空间、文件或权限变更。

| 方法 | 官方文档 | 报文与主要规则 |
| --- | --- | --- |
| `storage.create_space` | [5236](https://open.ys7.com/help/5236) | POST表单；创建时需bizType和spaceName |
| `storage.update_space` | [5237](https://open.ys7.com/help/5237) | PUT表单；未传字段按FAQ保持原值 |
| `storage.delete_space` | [5238](https://open.ys7.com/help/5238) | DELETE表单；删除空间及其文件，不可恢复 |
| `storage.spaces` | [5230](https://open.ys7.com/help/5230) | GET query；创建时间范围≤30天，lastSpaceId是游标 |
| `storage.set_file_retention` | [5232](https://open.ys7.com/help/5232) | POST表单；修改已有文件的保留期 |
| `storage.search_files` | [5233](https://open.ys7.com/help/5233) | POST表单只读查询，fileNodeIds > tagId > spaceIds+时间 |
| `storage.download_urls` | [5234](https://open.ys7.com/help/5234) | GET query；1–50个节点，有效期30–604800秒 |

全部成功码为整数meta.code=200。下载地址接口的accessToken在query，其余在Header；不能因属于同一产品而统一挪动。空间创建返回裸整数，更新可返回Boolean，文件/空间列表和下载Map各自保留data/raw，不强行统一响应形状。

## 使用约定

统一存储的永久保留为expireDays=-1，普通值1–5475；旧云录制专用空间的0永久语义不同。官网1975推荐新接入使用统一存储5236，不能把两套保留期规则混用。

spaceName按文档限制为中英文、数字、下划线，最多32字符，不接受旧云录制空间可用的中划线。可选配置None不发送，尤其更新不注入表格中的创建默认值；SDK要求至少一个显式变更字段。字段flowLimit只核验整数，不猜测未声明的上下限。

StorageFileQuery快照化调用方的序列，保留顺序和重复值；允许多种选择器同时传入并保留原字段，服务端按官方优先级处理。file_type发送String，file_child_type不限制为文档摘录的几个示例值（响应示例还出现06）。文件搜索不套用空间列表的30天上限，批量保留期和文件搜索不套用下载接口的50节点上限。

时间使用整秒naive datetime，源未声明时区；不自动换算。bizTypeList未明确多值编码，当前只提供文档列示的单业务类型过滤。spaceIds多空间CSV编码依据同接口集合参数风格推导，尚未经真实服务验证，此限制保留在来源登记中。

## 产品边界

导航中的[5341](https://open.ys7.com/help/5341)实际是云录制1.0解冻，需要projectId/fileId；[5342](https://open.ys7.com/help/5342)实际是云点播元数据查询。两者未混入统一存储引擎，不猜测新fileNodeId与旧fileId的映射。

文件下载返回带签名URL和毫秒级expireTime，URL应按凭证保管。SDK仅返回信息；不下载媒体、不自动解冻或翻页。修改空间保留期不等同于修改已有文件。所有写方法均不自动重放，即使源标记为幂等；企业套餐、主账号与托管/子账户限制仍由服务端校验，不假定任意token具备权限。
