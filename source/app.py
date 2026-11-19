"""无漏洞细节的厂商公告、旧镜像及资产输入来源。"""
from source.common import *

class Site(Provider):
    def initial(self):return {'revision':1,'index_version':1,'mismatch':False,'withdraw':False,'offline':False,'throttle':False,'assets_revision':1}
    def event(self,d):
        e=d['event']
        if e=='exclude':self.state['revision']+=1
        elif e=='index_shift':self.state['index_version']+=1
        elif e in ['mismatch','withdraw','offline','throttle']:self.state[e]=True
        elif e=='asset_upgrade':self.state['assets_revision']+=1
        else:return Reply({'error':'unknown_event'},400)
        return {'ok':True}
    def advisory(self,revision):
        return {'publisher':'vendor-a','id':'ADV-A','external_ids':['SYN-2026-01'],'revision':revision,'products':[{'id':'runtime','aliases':['runtime-old'],'component':'engine','affected':[{'introduced':'1.2.0','fixed':'1.4.0'}],'excluded':['1.3.2'] if revision>1 else [],'mitigation':'更新到受支持发行版'},{'id':'workbench','component':'engine','affected':[{'introduced':'2.0.0','fixed':'2.1.0'}],'excluded':[]}],'references':['/vendor/advisory?id=ADV-B'],'status':'withdrawn' if self.state['withdraw'] else 'published'}
    def get(self,p,q,h):
        s=self.state
        if p=='/':return web(page('厂商防御公告','<a href="/vendor/index">厂商索引</a><a href="/mirror/advisory?id=ADV-A">旧镜像</a><p>来源约定 /policy；资产 /assets。</p>'))
        if p=='/policy':return {'allowed_paths':['/vendor/','/mirror/'],'redirect_allowed_hosts':['127.0.0.1','localhost'],'credential_scope':'/vendor/','request_interval_ms':100,'external_redirect':'/vendor/redirect'}
        if p=='/assets':return {'revision':s['assets_revision'],'assets':[{'id':'node-a','package':'runtime-old','version':'1.3.2' if s['assets_revision']==1 else '1.4.0','components':{'engine':'1.3.2'}},{'id':'node-b','package':'workbench','version':'2.0.1','components':{'engine':'2.0.1'}}]}
        if p=='/vendor/redirect':return Reply(b'',302,headers={'Location':'https://unapproved.example.test/advisory'})
        if p=='/vendor/index':
            if s['throttle']:return Reply({'error':'rate_limited'},429,headers={'Retry-After':'3'})
            ids=['ADV-A','ADV-B'] if s['index_version']==1 else ['ADV-C','ADV-A','ADV-B']
            offset=int(q.get('page','0'));return {'index_revision':s['index_version'],'total':len(ids),'items':[{'id':x,'url':'/vendor/advisory?id='+x} for x in ids[offset:offset+1]],'next':offset+1<len(ids)}
        if p in ['/vendor/advisory','/mirror/advisory']:
            if s['offline'] and p.startswith('/vendor'):return Reply({'error':'unavailable'},503)
            rev=1 if p.startswith('/mirror') else s['revision'];record=self.advisory(rev)
            if q.get('id','ADV-A')!='ADV-A':record={**record,'id':q['id'],'products':[],'references':[]}
            raw=dump(record).encode();expected=digest(raw)
            return web(page('维护公告','<p id="source">原始发布 vendor-a；公告 '+escaped(record['id'])+'；修订 '+str(rev)+'</p><p id="status">'+record['status']+'</p><a id="machine" data-sha256="'+expected+'" href="/machine?id='+record['id']+'&revision='+str(rev)+'">机器可读公告</a><p>此页仅提供防御维护适用性信息。</p>'))
        if p=='/machine':
            rev=int(q['revision'])+int(s['mismatch']);r=self.advisory(rev)
            if q['id']!='ADV-A':r={**r,'id':q['id'],'products':[],'references':[]}
            return r
        return super().get(p,q,h)

if __name__=='__main__':main(Site)
