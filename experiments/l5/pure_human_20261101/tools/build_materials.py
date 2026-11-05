from pathlib import Path
import re,json,hashlib
R=Path('/home/dzy/code/TOSEM2026/AUCMR/experiments/l5/pure_human_20261101')
# Per-page normalized text is an extraction index; original PDFs and layout text remain untouched.
docs={}
for p in (R/'screening').glob('*.txt'):
 lines=p.read_text().splitlines(); page=0; rows=[]
 for i,t in enumerate(lines):
  m=re.match(r'=== PDF PAGE (\d+) ===',t)
  if m:page=int(m[1])
  rows.append({'line':i+1,'page':page,'text':t})
 docs[p.stem]=rows

def find(rows,s,start=0):
 return next(i for i in range(start,len(rows)) if rows[i]['text']==s)
def page(rows,n):return next(i for i,r in enumerate(rows) if r['page']==n)
def spans(rows,a,b):return {'start_line':a+1,'end_line_exclusive':b+1,'pages':sorted({r['page'] for r in rows[a:b]})}
def clean(rows,a,b):
 out=[]
 for r in rows[a:b]:
  t=r['text']
  if t.startswith('=== PDF PAGE ') or t.isdigit():continue
  if re.match(r'^(GAMMA-J Web Store \d+|\d+ Version 1\.0|Software Requirements Specification for KeePass Password Safe Page \d+|Battelle Northwest, 2003 Page \d+|11/01/2001 \d+|Software Requirements Specification CS4311 Date: Page|Automated Diagnostics Version:|Software Requirements Specification Date:|Software Requirements Specification 1-1\.DOC|Software Requirements Specification$|Software Requirements Specification for Page \d+|Internetworking of CDNs through Peering$|\d+ CHAPTER \d+\.|\d+\.\d+\. [A-Z ]+ \d+$)',t):continue
  out.append(t)
 return '\n'.join(out).strip()
cat=[]; recipes={}
def add(doc,local,title,actor,a,b,verdict,basis,source_ids,notes=''):
 rows=docs[doc]; key=doc.split(' - ')[1].replace(' ','_'); cid=f'{key}-{local}'
 cat.append({'id':cid,'document':doc,'original_id':local,'title':title,'primary_actor':actor,'actor_basis':'原文 Actors/Primary actor/Basic Flow 的发起角色；给定任务信息，不评识别能力。','B_span':spans(rows,a,b),'B_text':clean(rows,a,b),'decision':verdict,'reason':basis,'independent_requirement_locations':source_ids,'notes':notes,'completeness':'保留原字段及缺失项；不按模板完整程度筛选。'})
def src(doc,ranges,hidden,parallel,typ):
 rows=docs[doc]; chunks=[]
 for a,b in ranges:
  chunks.append({'span':spans(rows,a,b),'text':clean(rows,a,b)})
 recipes[doc]={'chunks':chunks,'hidden':hidden,'parallel_audit':parallel,'material_type':typ}
# Inventory
D='0000 - inventory';r=docs[D]
src(D,[(page(r,3),page(r,9))],'整个 §7（PDF 9–28）及其流程图，不输入 ER 图。','§5 是独立角色权限/功能要求，未保留 §7 正文或图。','课程项目 SRS')
inv=[('MOD.UC','Modify Use Case','Inventory Admin',9,11,'§5.3 修改除 ID 外所有字段、批量修改'),('EDT.UC','Edit Use Case','Inventory Admin',11,13,'§5.2 按 inventory/owner 权限编辑资产'),('ANI.UC','Add New Asset Use Case','Inventory Admin',13,15,'§5.4 库存管理员新增资产、owner 设定、批量输入'),('CRQ.UC','Create request Use Case','User',15,17,'§5.5 授权用户发借用/预订请求，等待批准'),('RTI.UC','Returning Asset','Inventory Admin',17,19,'§5.6 检查归还资产并更新库存'),('APR.UC','Approving Request','Inventory Admin',19,21,'§5.8 显示待批请求、授权批准、通知、执行后更新'),('CHP.UC','change permissions Use Case','Administrator',21,23,'§5.10 管理员分配权限、成为库存管理员'),('ATH.UC','Authentication Use Case','User',23,25,'§5.9 用户名密码认证'),('SRCH.UC','Search','User',25,27,''),('REP.UC','Create Reports Use Case','User',27,29,'')]
for oid,title,actor,p,q,basis in inv:
 # The textual table precedes the diagram; retain table content and notes only.
 a=next(i for i in range(page(r,p),page(r,q)) if r[i]['text'].startswith('Name:'));b=find(r,'We suppose that Hosting system and servers support all the operations',a)+1
 # table ends at Notes line (notes may continue); manually identify diagram by its leading actor/ID repetition later.
 add(D,oid,title,actor,a,b,'INCLUDE' if basis else 'EXCLUDE',basis or '独立需求只给功能/报表名称，未提供核心交互处理。',basis,'Modify/Edit 相互接近，按同项目相关目标处理；CRQ 的 Actors 与正文发起角色不一致，保留原缺陷。')
# Agentmom
D='2003 - agentmom';r=docs[D];s=find(r,'3 Specific Requirements');fr=find(r,'3.2 Specific Requirement')
src(D,[(page(r,3),s),(fr,len(r))],'整个 §3.1 的四个用例、示例及配图（PDF 5–7）。','§1/2 定义与 §3.2 通信功能约束不按用例步骤排列。','学术软件框架 SRS')
# Find actual section headings adaptively.
heads=[i for i,x in enumerate(r) if x['page']>=5 and x['text'].startswith('Use Case ')]
for j,us in enumerate(heads):
 ue=heads[j+1] if j+1<len(heads) else fr
 add(D,f'UC{j+1}',r[us]['text'].split(': ',1)[1],'Agent',us,ue,'INCLUDE','§2.2 模式选择、§2.5 地址/密钥假设、§3.2 消息发送/接收/加入离开/加密规则。',f'§3.2.{[2,1,2,3][j]} / §3.2.4')
# Triangle
D='2005 - triangle';r=docs[D];a=page(r,6);end=find(r,'6 Traceability',a)
ucstarts=[i for i in range(a,end) if re.match(r'^3\.\d+\.2 Use Case',r[i]['text'])]
blocks=[]
for st in ucstarts:
 en=next(i for i in range(st+1,end) if re.match(r'^3\.\d+\.3 Functional Requirement',r[i]['text']))
 blocks.append((st,en))
ranges=[];cursor=a
for st,en in blocks:ranges.append((cursor,st));cursor=en
ranges.append((cursor,end))
src(D,ranges,'全部 §3.x.2 用例块（包括两个 UC-011）；附录重复需求/图不输入。','保留 §3.x.1 独立功能定义及 §3.x.3 约束；其内容未逐步重述用例（如保存对话框只在 B）。','课程游戏 SRS')
for k,(st,en) in enumerate(blocks,1):
 starts=[i for i in range(st+1,en) if r[i]['text'].startswith('UC -')]
 for j,us in enumerate(starts):
  ue=starts[j+1] if j+1<len(starts) else en
  title=r[us]['text'];title=re.sub(r'^UC -\s*\d*\s*','',title);title=re.sub(r' V 1\.0$','',title)
  if k==3:title+=' menu'
  local=f'UC-{k:03d}'+('-load' if k==10 and j==1 else '')
  if k==10:local='UC-010' if j==0 else 'UC-011-load'
  if k==11:local='UC-011-help'
  actor='AI Developer' if k==7 else 'Game User'
  basis=f'§3.{k}.1、§3.{k}.3：独立功能目的、操作/处理及结果；补充 §2.2 与 §5.2。'
  add(D,local,title,actor,us,ue,'INCLUDE' if k!=1 else 'EXCLUDE',basis if k!=1 else '启动交互与加载失败只在 B；GUI 一般说明不足以给出启动行为。',f'§3.{k}.1 / §3.{k}.3', '原文 UC-011 重号用位置区分，不改写原文。')
# VUB
D='2008 - vub';r=docs[D];st=find(r,'3.2.1 Log in to the system from the internet',page(r,20));en=next(i for i in range(st,len(r)) if r[i]['text'].startswith('3.3 '))
heads=[i for i in range(st,en) if re.match(r'^3\.2\.\d+ ',r[i]['text'])]
src(D,[(page(r,8),page(r,9)),(page(r,12),page(r,13)),(page(r,15),page(r,22)),(page(r,24),page(r,26))],'整个 §3.2（PDF 26–36）；所有用例图与未选 UI 图隔离。','§2.3 独立产品功能按角色组织，保留规范事实；没有导入 §3.2 的步骤/异常/结果。','课程出版物管理 SRS')
elig={1:'§2.3.1 登录有效账户',2:'§2.1 / §2.3.2 IP 网络自动识别并显示搜索',3:'§2.1、§2.3.1 注册字段、验证邮件',4:'§2.3.3 退出后按 IP 显示搜索或登录',5:'§2.3.1 Retrieve password；原 B 标题 Change password 实为遗忘密码恢复',6:'§2.3.2 关键词/作者等检索返回结果',7:'§2.3.2 显示/排序命中出版物',10:'§2.3.2 导出/下载一项',11:'§2.3.2 导出/下载多项',14:'§1.2 全文可用时访问；§2.3.2 下载格式',19:'§2.3.3 修改邮箱/密码等个人信息',21:'§2.3.4 仅编辑本人已上传出版物',22:'§2.3.4 上传、提取元数据、手工修正',24:'§2.3.5 用户级别升降与部门权限',30:'§2.3.5 修改辖区 group permissions'}
for j,us in enumerate(heads):
 n=j+1;ue=heads[j+1] if j+1<len(heads) else en;title=re.sub(r'^3\.2\.\d+ ','',r[us]['text'])
 actorline=next((r[i]['text'] for i in range(us,ue) if re.search(r'Actors\s*:',r[i]['text'])),'')
 actor=re.split(r'Actors\s*:',actorline)[-1].strip()
 add(D,f'3.2.{n}',title,actor,us,ue,'INCLUDE' if n in elig else 'EXCLUDE',elig.get(n,'移除 §3.2 后，仅有概括性能力/实体名称，缺少该目标独立交互与可观察结果依据。'),elig.get(n,'§1.2 / §2.3 已检查'))
# PNNL
D='2003 - pnnl';r=docs[D];st=find(r,'3.1.1 Start and Stop Diagnostics Use Case');en=find(r,'3.2 Functionality',st)
heads=[i for i in range(st,en) if re.match(r'^3\.1\.\d+ [A-Z]',r[i]['text'])]
src(D,[(page(r,11),find(r,'2.5 Use Cases',page(r,11)) if any(x['text']=='2.5 Use Cases' for x in r) else page(r,13)),(en,find(r,'3.2.2 Preprocessing',en)),(find(r,'3.2.3.5 Any and all errors associated with failures of the diagnostic algorithms shall be logged as'),page(r,45))], '§2.5 用例概述、整个 §3.1（PDF 15–22）、诊断算法附录隔离。','§3.3/3.5 是独立界面状态与控件规范，包含相同业务事实但并非 B 的有序场景副本；不采用 UI 图内标签。','工业 HVAC SRS 中局部界面管理需求')
for j,us in enumerate(heads):
 ue=heads[j+1] if j+1<len(heads) else en;title=re.sub(r'^3\.1\.\d+ ','',r[us]['text']).removesuffix(' Use Case')
 add(D,f'3.1.{j+1}',title,'Administrator' if j in [0,3] else 'User',us,ue,'INCLUDE','§3.2.1 角色权限、§3.3 状态反馈、§3.5 独立界面规范和 §3.6 认证。', ['§3.5.1.6–10','§3.5.1.2–3,17–22','§3.5.1.23–31','§3.5.1.11–16','§3.6'][j], '原文 176 页，但任务仅界面/权限局部；不声称完整复杂诊断文档实验。')
# HATS
D='2001 - hats';r=docs[D];st=find(r,'2.2.2.1. Use-case 1: Parse',page(r,15));en=find(r,'2.3. General Constraints',st)
heads=[i for i in range(st,en) if re.match(r'^2\.2\.2\.\d+\. Use-case',r[i]['text'])]
a=find(r,'[SRSreq 96] To perform a transformation in an application, the HATS-GUI shall execute the following');b=find(r,'[SRSreq 97] <deleted>.',a)
src(D,[(page(r,13),find(r,'2.2. Use Cases',page(r,13))),(page(r,28),a),(b,page(r,42))],'§2.2 全部用例、图、附录协议与图；SRSreq96 有序执行流程作为平行场景保守移除。','其他保留内容为独立菜单/配置数据/节点显示规则。SRSreq69/74 等与用例共享规范事实，但没有复制场景的多角色步骤、ALT 编号与完整异常序列。','课程 HATS 图形前端 SRS')
for j,us in enumerate(heads):
 ue=heads[j+1] if j+1<len(heads) else en;title=r[us]['text'].split(': ',1)[1]
 add(D,f'UC{j+1}',title,'User',us,ue,'INCLUDE','独立菜单刺激响应和对象/显示规范包含核心操作及响应；移除流程型 SRSreq96 后仍有功能依据。',['§3.2.2.1.4 / §3.2.3.2','§3.2.2.1.4 / §3.2.3.2','SRSreq74–79','SRSreq69','SRSreq70–79,94','SRSreq80 / §3.2.1.1','§3.2.3.3 显示规则','§3.2.3.3.5 显示文本规则','SRSreq85 / §3.2.3.3'][j], '目标可含多个 scenario，按原用例整体计一个；不拆分凑数。')
# Central Trading
D='2007 - central trading system';r=docs[D];heads=[i for i,x in enumerate(r) if x['text'].startswith('Use-case ')];end=find(r,'2.2.3 Use-case diagram for Central Trading System function')
src(D,[(page(r,3),find(r,'2.2 User Scenarios Specification')),(page(r,10),page(r,14)),(page(r,15),len(r))],'§2.2 五个用例及用例图；DFD/状态图的图像不输入。','保留模块职责、数据字典、接口及验证准则；不含 B 的 Scenario/Exception/Open issues 文本。','课程交易子系统 SRS')
for j,us in enumerate(heads):
 ue=heads[j+1] if j+1<len(heads) else end;actor=r[us+1]['text'].removeprefix('Primary actor ')
 add(D,f'UC{j+1}',r[us]['text'][9:],actor,us,ue,'EXCLUDE' if j==3 else 'INCLUDE','Save trade information 的 B 主体是 Security Account Management，独立 CTS 需求未提供该外部系统的保存交互。' if j==3 else '§3 模块文字说明、§4 数据字典、§7 接口与匹配/返回规则独立支持核心行为。','§3–4 / §7.1–7.2')
# KeePass
D='2008 - keepass';r=docs[D];st=find(r,'3. System Features',page(r,10));heads=[i for i in range(st,len(r)) if re.match(r'^3\.(?:[1-9]|1[0-4]) ',r[i]['text'])]
ranges=[(page(r,7),page(r,11))];spec=[]
for j,us in enumerate(heads):
 n=j+1;bs=next(i for i in range(us,len(r)) if 'Stimulus/Response Sequences' in r[i]['text']);be=next(i for i in range(bs,len(r)) if r[i]['text'].startswith(f'3.{n}.3 '));fe=next((i for i in range(be+1,len(r)) if re.match(r'^3\.\d+ ',r[i]['text'])),len(r))
 ranges.append((be,fe));spec.append((n,us,bs,be))
src(D,ranges,'§3.1–3.14 的 .1 Description 及 .2 全部刺激/响应序列均隐藏；后续无用例功能不作为 B。','局部 Description 经常平行复述用例，全部保守隔离；只保留全局 §2.2–2.6 与独立 .3 功能约束。','课程 KeePass 产品 SRS')
for n,us,bs,be in spec:
 title=re.sub(r'^3\.\d+ ','',r[us]['text'])
 add(D,f'3.{n}',title,'User',us,be,'INCLUDE' if n not in [7,8] else 'EXCLUDE','全局产品功能与独立 REQ 约束保留操作对象、发起动作及结果。' if n not in [7,8] else '隔离平行 Description 后，修改/删除组只剩能力名称，缺少该目标独立处理与响应。',f'§2.2 / §3.{n}.3','§3.15 之后明确没有用例，不计候选。')
# Gamma
D='0000 - gamma j';r=docs[D];st=page(r,26);en=page(r,41)
heads=[]
for i in range(st,en):
 if r[i]['text']=='Goal:':
  us=i-1
  while r[us]['text'].startswith(('=== PDF PAGE','GAMMA-J Web Store')):us-=1
  heads.append(us)
src(D,[(page(r,4),find(r,'4. External Interface Requirements',page(r,8))),(page(r,43),len(r))],'整个 Appendix B（PDF 26–40）包括后段重复六例；全部 UI mockup/analysis 图不输入。','全局角色、功能需求、数据字典为独立事实。重复 customer 组全部隔离，不以其中一稿作为输入。','课程 Web store SRS')
seen={};eligible={'Register Customer','Edit Customer Details','Add Item To Cart','Checkout An Order','Change User Properties','Add Product','Remove Product','Update Product Attributes'}
for j,us in enumerate(heads):
 ue=heads[j+1] if j+1<len(heads) else en;title=r[us]['text'];a=find(r,'Actors:',us);actor=r[a+1]['text']
 duplicate=title in seen;ver='DUPLICATE' if duplicate else ('INCLUDE' if title in eligible else 'EXCLUDE')
 add(D,f'occ{j+1:02d}',title,actor,us,ue,ver,'重复同名同任务正文；保留较早出现稿作为唯一候选，不按质量选择。' if duplicate else ('§2.2–2.3 角色功能、§3 功能要求、Appendix E 字段规范支持核心操作及结果。' if ver=='INCLUDE' else '隐藏 UI/B 后，只有能力名称或泛化维护/API 愿景，缺少该目标的独立交互处理依据。'),'§2.2–2.3 / §3 / Appendix E',f'此前位置：{seen.get(title, "首次")}')
 seen.setdefault(title,j+1)
# Peering
D='2008 - peering';r=docs[D];st=find(r,'3.1 Service Registration',page(r,10));heads=[i for i in range(st,len(r)) if re.match(r'^3\.[1-6] ',r[i]['text'])]
ranges=[(page(r,3),page(r,4)),(page(r,7),page(r,10))]
for j,us in enumerate(heads):
 n=j+1;be=next(i for i in range(us,len(r)) if r[i]['text'].startswith(f'3.{n}.7 Functional Requirements'));ue=heads[j+1] if j+1<len(heads) else len(r);ranges.append((be,ue))
 # Descriptions before Actors are also part of hidden B.
 actor_idx=next(i for i in range(us,be) if r[i]['text'].startswith(f'3.{n}.1 Actors'))
 actor='Web server' if n in [1,2] else ('Mediator' if n in [3,5] else 'Peering Agent')
 add(D,f'3.{n}',re.sub(r'^3\.\d+ ','',r[us]['text']),actor,us,be,'EXCLUDE' if n==6 else 'INCLUDE','移除 §2.1 平行流程与本用例后，终止/重组的核心条件与处理依据不足。' if n==6 else '§2.2 组件职责与协作规则、独立 .7 功能约束仍提供该局部目标的核心行为。','§2.2 / §3.%s.7'%n,'组件为主要参与者；不把子组件识别计入成绩。')
src(D,ranges,'§2.1 六阶段概述及 Fig1 为平行过程而隐藏；§3.x.1–.6 连同引导描述隔离。','§2.2 按组件独立规定职责/协作，未复制具体场景的完整顺序；.7 保留格式、协议、约束。','研究型 CDN peering 原型 SRS')
# Conservative role-boundary decision: the active component differs from listed external actors.
for x in cat:
 if x['document']=='2008 - peering' and x['original_id'] in ['3.3','3.4','3.5']:
  x['decision']='PENDING_BOUNDARY';x['reason']='原文 Actors 列外部组件，但生成对象是 Mediator/PA 内部行为；不能直接把系统内部主动组件当主要参与者。当前不纳入，避免擅改系统边界。'
 if x['document']=='2008 - keepass':
  # Preserve native feature name alongside its response block without adding facts.
  pass
(R/'screening/catalog_draft.json').write_text(json.dumps(cat,ensure_ascii=False,indent=2))
(R/'screening/source_recipes_draft.json').write_text(json.dumps(recipes,ensure_ascii=False,indent=2))
print('COUNTS', {d:sum(x['document']==d for x in cat) for d in docs})
