"""Rename, physical registers, ALUs, committed memory, and CPU integration."""
import xml.etree.ElementTree as ET

from circuit import Circuit, Net
from backend import RS_FIELDS, ROB_FIELDS


def inputs(c, fields):
    return {k: c.input(k, w) for k, w in fields.items()}


def first(c, flags, width=6):
    valid = c.const(0)
    index = c.const(0, width)
    for i in reversed(range(len(flags))):
        index = c.mux(flags[i], index, c.const(i, width))
        valid = c.lor(valid, flags[i])
    return valid, index


def build_alu():
    c = Circuit('ALU', '16-bit ALU | signed compare | word-addressed branches')
    s = inputs(c, {'op':4,'a':16,'b':16,'imm':16,'pc':16})
    op,a,b,imm,pc = [s[k] for k in ('op','a','b','imm','pc')]
    pc1 = c.inc(pc)
    add = c.add(a,b)
    addi = c.add(a,imm)
    rel = c.add(pc1,imm)
    eq = c.eq(a,b)
    slt = c.extend(c.compare(a,b,'lt',True),16)
    slti = c.extend(c.compare(a,imm,'lt',True),16)
    z = c.const(0,16)
    result = c.choose(op,[z,add,c.sub(a,b),c.land(a,b),c.lor(a,b),
                          c.xor(a,b),slt,addi,z,z,z,z,pc1,pc1,slti,z])
    taken = c.lor(c.land(c.is_(op,10),eq),c.land(c.is_(op,11),c.inv(eq)),
                  c.is_(op,12),c.is_(op,13))
    target = c.mux(c.is_(op,13),rel,addi)
    for name,value in {'result':result,'next':c.mux(taken,pc1,target),
                        'target':target,'taken':taken,'addr':addi}.items():
        c.output(name,value)
    return c


def build_physical_cell():
    c = Circuit('PhysicalRegister', 'Physical value | scoreboard | free-list bit')
    s = inputs(c, {'clk':1,'rst':1,'enable':1,'clear':1,'id':6,'initial_free':1,
                   'rebuild_free':1,'alloc0':6,'alloc1':6,'use0':1,'use1':1,
                   'free_v':1,'free_tag':6,
                   'c0_v':1,'c0_tag':6,'c0_data':16,
                   'c1_v':1,'c1_tag':6,'c1_data':16})
    allocated = c.lor(c.land(s['use0'],c.eq(s['id'],s['alloc0'])),
                      c.land(s['use1'],c.eq(s['id'],s['alloc1'])))
    released = c.land(s['free_v'],c.eq(s['id'],s['free_tag']))
    w = [c.land(s[f'c{k}_v'],c.eq(s['id'],s[f'c{k}_tag'])) for k in range(2)]
    value = c.reg('value',16,c.mux(w[1],s['c0_data'],s['c1_data']),
                  c.land(s['enable'],c.lor(*w)))
    ready = c.ref('ready')
    next_ready = c.mux(allocated,c.lor(ready,*w),c.const(0))
    next_ready = c.mux(s['clear'],next_ready,c.const(1))
    ready = c.reg('ready',1,next_ready,c.lor(s['enable'],s['clear']),init=1)
    free = c.ref('free')
    next_free = c.land(c.lor(free,released),c.inv(allocated))
    next_free = c.mux(s['clear'],next_free,s['rebuild_free'])
    next_free = c.mux(s['rst'],next_free,s['initial_free'])
    free = c.reg('free',1,next_free,c.lor(s['enable'],s['clear'],s['rst']),c.const(0))
    for name,net in {'value':value,'ready':ready,'free':free}.items(): c.output(name,net)
    return c


def build_rename(cell):
    c = Circuit('Rename_PRF', 'RAT / committed RAT / FL64 / dual-write PRF64')
    common = inputs(c, {'clk':1,'rst':1,'enable':1,'clear':1,
                        'accept0':1,'accept1':1,'commit_v':1,'commit_rw':1,
                        'commit_rd':3,'commit_new':6,'commit_old':6,'commit_data':16,
                        'c0_v':1,'c0_tag':6,'c0_data':16,'c1_v':1,'c1_tag':6,'c1_data':16})
    ds = [inputs(c,{f'{n}{i}':w for n,w in {'rd':3,'rs1':3,'rs2':3,'rw':1}.items()}) for i in range(2)]
    lane = [{n:ds[i][n+str(i)] for n in ('rd','rs1','rs2','rw')} for i in range(2)]
    use = [c.land(common[f'accept{i}'],lane[i]['rw']) for i in range(2)]
    crat = [c.const(0,6)]
    rat = [c.const(0,6)]
    arf = [c.const(0,16)]
    for i in range(1,8):
        commit = c.land(common['commit_v'],common['commit_rw'],c.is_(common['commit_rd'],i))
        cm = c.reg(f'committed_map_r{i}',6,common['commit_new'],commit,init=i)
        crat.append(cm)
        arf.append(c.reg(f'committed_r{i}',16,common['commit_data'],commit))
        w = [c.land(use[k],c.is_(lane[k]['rd'],i)) for k in range(2)]
        nxt = c.mux(w[1],c.ref('new0',6),c.ref('new1',6))
        nxt = c.mux(common['clear'],nxt,cm)
        rat.append(c.reg(f'RAT_r{i}',6,nxt,c.lor(*w,common['clear']),init=i))
    released = c.land(common['commit_v'],common['commit_rw'],c.nonzero(common['commit_old']))
    cells = [{'value':c.const(0,16),'ready':c.const(1),'free':c.const(0)}]
    for p in range(1,64):
        used_committed = c.lor(*[c.is_(r,p) for r in crat[1:]])
        args = {k:common[k] for k in ('clk','rst','enable','clear','c0_v','c0_tag','c0_data','c1_v','c1_tag','c1_data')}
        args.update(id=c.const(p,6),initial_free=c.const(int(p>=8)),rebuild_free=c.inv(used_committed),
                    alloc0=c.ref('new0',6),alloc1=c.ref('new1',6),use0=use[0],use1=use[1],
                    free_v=released,free_tag=common['commit_old'])
        cells.append(c.instance(cell,args,f'p{p:02d}'))
    free = [p['free'] for p in cells]
    has0,first0 = first(c,free)
    has1,first1 = first(c,[c.land(f,c.inv(c.is_(first0,i))) for i,f in enumerate(free)])
    new0 = c.alias('new0',c.mux(lane[0]['rw'],c.const(0,6),first0))
    new1 = c.alias('new1',c.mux(lane[1]['rw'],c.const(0,6),c.mux(lane[0]['rw'],first0,first1)))
    c.output('available0',c.lor(c.inv(lane[0]['rw']),has0))
    c.output('available1',c.lor(c.inv(lane[1]['rw']),c.mux(lane[0]['rw'],has0,has1)))
    c.output('new0',new0)
    c.output('new1',new1)
    old0 = c.choose(lane[0]['rd'],rat)
    old1 = c.choose(lane[1]['rd'],rat)
    old1 = c.mux(c.land(lane[0]['rw'],c.eq(lane[0]['rd'],lane[1]['rd'])),old1,new0)
    c.output('old0',c.mux(lane[0]['rw'],c.const(0,6),old0))
    c.output('old1',c.mux(lane[1]['rw'],c.const(0,6),old1))
    for i in range(2):
        for src,suffix in [('rs1','j'),('rs2','k')]:
            tag = c.choose(lane[i][src],rat)
            dep = c.const(0) if i==0 else c.land(lane[0]['rw'],c.eq(lane[0]['rd'],lane[1][src]))
            tag = c.mux(dep,tag,new0)
            value = c.choose(tag,[p['value'] for p in cells])
            ready = c.choose(tag,[p['ready'] for p in cells])
            for k in (1,0):
                hit = c.land(common[f'c{k}_v'],c.nonzero(common[f'c{k}_tag']),c.eq(tag,common[f'c{k}_tag']))
                value = c.mux(hit,value,common[f'c{k}_data'])
                ready = c.lor(ready,hit)
            ready = c.land(ready,c.inv(dep))
            c.output(f'q{suffix}{i}',tag)
            c.output(f'd{suffix}{i}',value)
            c.output(f'v{suffix}{i}',ready)
    for i,r in enumerate(arf): c.output(f'r{i}',r)
    return c


def build_store_buffer():
    c = Circuit('StoreBuffer', 'Eight speculative stores | youngest older forwarding')
    s = inputs(c,{'clk':1,'rst':1,'enable':1,'clear':1,'push':1,'addr':16,'data':16,
                  'sn':8,'load_addr':16,'load_sn':8,'commit':1,'commit_sn':8})
    reset = c.lor(s['rst'],s['clear'])
    valid, rows = [],[]
    for i in range(8):
        v=c.ref(f'valid{i}')
        wr=c.ref(f'write{i}')
        word=c.reg(f'word{i}',40,c.pack(s['addr'],s['data'],s['sn']),wr,reset)
        row={'addr':c.bits(word,0,16),'data':c.bits(word,16,16),'sn':c.bits(word,32,8)}
        pop=c.land(s['commit'],v,c.eq(row['sn'],s['commit_sn']))
        c.reg(v.name,1,wr,c.lor(wr,pop),reset)
        valid.append(v)
        rows.append(row)
    any_free,which=first(c,[c.inv(v) for v in valid],3)
    for i in range(8):
        c.alias(f'write{i}',c.land(s['enable'],s['push'],any_free,c.is_(which,i)))
    count = c.const(0,4)
    for v in valid: count=c.add(count,c.extend(v,4))
    match=c.const(0)
    fdata=c.const(0,16)
    fsn=c.const(0,8)
    cv=c.const(0)
    ca=c.const(0,16)
    cd=c.const(0,16)
    for v,row in zip(valid,rows):
        older=c.bits(c.sub(row['sn'],s['load_sn']),7,1)
        hit=c.land(v,older,c.eq(row['addr'],s['load_addr']))
        newer=c.bits(c.sub(fsn,row['sn']),7,1)
        pick=c.land(hit,c.lor(c.inv(match),newer))
        fsn=c.mux(pick,fsn,row['sn'])
        fdata=c.mux(pick,fdata,row['data'])
        match=c.lor(match,hit)
        cm=c.land(v,c.eq(row['sn'],s['commit_sn']))
        cv=c.lor(cv,cm)
        ca=c.mux(cm,ca,row['addr'])
        cd=c.mux(cm,cd,row['data'])
    for k,v in {'full':c.inv(any_free),'count':count,'forward':match,'forward_data':fdata,
                 'commit_found':cv,'commit_addr':ca,'commit_data':cd}.items(): c.output(k,v)
    return c


def build_memory(data=None):
    if data is not None:
        data = list(data)
        if len(data) > 65536 or any(not 0 <= word <= 0xffff for word in data):
            raise ValueError('data image must contain at most 65536 unsigned 16-bit words')
    c=Circuit('DataMemory','64K x 16 RAM | F000-F0FF mapped I/O | commit-only writes')
    s=inputs(c,{'clk':1,'rst':1,'read_addr':16,'commit':1,'write_addr':16,'write_data':16,
                'debug_addr':16,'read_enable':1,'io_read_data':16})
    io_read_addr=c.is_(c.bits(s['read_addr'],8,8),0xf0)
    io_write_addr=c.is_(c.bits(s['write_addr'],8,8),0xf0)
    io_write=c.land(s['commit'],io_write_addr)
    io_read=c.land(s['read_enable'],c.inv(s['commit']),io_read_addr)
    ram_write=c.land(s['commit'],c.inv(io_write_addr))
    # A generated data image is the program's initial data segment. Keep it
    # across CPU reset so constants remain available after the normal reset
    # pulse; an image-less RAM retains the historical reset-to-zero behavior.
    ram_clear=c.const(0) if data is not None else s['rst']
    addr=c.mux(s['commit'],s['read_addr'],s['write_addr'])
    c.output('io_addr',addr)
    c.output('io_write_data',s['write_data'])
    c.output('io_read',io_read)
    c.output('io_write',io_write)
    # Native RAM geometry is checked by LogisimHarness --catalog.
    for suffix,address in [('main',addr),('debug',c.mux(s['commit'],s['debug_addr'],s['write_addr']))]:
        x,y=c.loc(height=300)
        comp=c.comp(4,'RAM',x,y,**{'addrWidth':16,'dataWidth':16,'appearance':'classic',
                     'databus':'bibus','enables':'byte','asyncread':True,
                     'clearpin':True,'label':'DMEM_'+suffix})
        if data is not None:
            contents = 'addr/data: 16 16\n' + ' '.join(f'{word:04x}' for word in data) + '\n'
            ET.SubElement(comp, 'a', name='contents').text = contents
        c.ports([(x,y+10,address),(x,y+90,s['write_data']),
                 (x,y+70,s['clk']),(x,y+50,ram_write),(x,y+60,c.const(1))])
        c.tunnel(x+40,y-30,ram_clear,'south')
        c.wire((x+40,y-30),(x+40,y))
        n=c.new(16,'ram_'+suffix)
        c.port(x+240,y+90,n,True)
        c.output('data' if suffix=='main' else 'debug_data',
                 c.mux(io_read,n,s['io_read_data']) if suffix=='main' else n)
    return c


def build_cpu(front,decoder,rs,lsrs,rob,alu,rename,sb,mem):
    c=Circuit('CPU_Core','Dual issue / out-of-order execute / precise ordered retirement')
    clk=c.input('clk')
    rst=c.input('rst')
    run=c.input('run')
    debug_addr=c.input('debug_addr',16)
    io_read_data=c.input('io_read_data',16)
    halted=c.ref('halted')
    recovery=c.ref('recovery')
    go=c.land(run,c.inv(halted),c.inv(recovery),c.inv(rst))
    clear=c.lor(recovery,halted)
    retire=c.ref('retire')
    branch_miss=c.ref('branch_miss')
    halt_commit=c.ref('halt_commit')
    c.reg('halted',1,c.const(1),halt_commit)
    c.reg('recovery',1,branch_miss,c.const(1))
    recovery_pc=c.reg('recovery_pc',16,c.ref('retire_next',16),branch_miss)
    retirement_sn=c.ref('retirement_sn',8)
    c.reg('retirement_sn',8,c.inc(retirement_sn),retire)
    global_sn=c.ref('global_sn',8)
    accepted=c.add(c.extend(c.ref('accept0'),2),c.extend(c.ref('accept1'),2))
    c.reg('global_sn',8,c.mux(recovery,c.add(global_sn,c.extend(accepted,8)),retirement_sn),
          c.lor(go,recovery))
    fetched=c.instance(front,{'clk':clk,'rst':rst,'enable':go,'flush':recovery,'recover_pc':recovery_pc,
                              'consume':accepted,'bp_we':c.ref('bp_we'),'bp_pc':c.ref('retire_pc',16),
                              'bp_target':c.ref('retire_target',16),'bp_taken':c.ref('retire_taken')},'fetch')
    dec=[]
    for i in range(2):
        d=c.instance(decoder,{'instr':fetched[f'instr{i}'],'pc':fetched[f'pc{i}'],
                              'pred':fetched[f'pred{i}']},f'decode{i}')
        d['rw']=c.land(d['rw'],c.nonzero(d['rd']))
        d['op']=d['opcode']
        dec.append(d)
    rename_args={'clk':clk,'rst':rst,'enable':go,'clear':recovery,
                 'accept0':c.ref('accept0'),'accept1':c.ref('accept1'),
                 'commit_v':retire,'commit_rw':c.ref('retire_rw'),'commit_rd':c.ref('retire_rd_logic',3),
                 'commit_new':c.ref('retire_rd_new',6),'commit_old':c.ref('retire_rd_old',6),
                 'commit_data':c.ref('retire_result',16)}
    for k in range(2):
        rename_args.update({f'c{k}_v':c.ref(f'c{k}_valid'),f'c{k}_tag':c.ref(f'c{k}_rd',6),
                            f'c{k}_data':c.ref(f'c{k}_result',16)})
        rename_args.update({n+str(k):dec[k][n] for n in ('rd','rs1','rs2','rw')})
    ren=c.instance(rename,rename_args,'rename')
    lane_rs=[]
    lane_rob=[]
    for i in range(2):
        sn=global_sn if i==0 else c.inc(global_sn)
        lane_rs.append({'op':dec[i]['op'],'rd':ren[f'new{i}'],'qj':ren[f'qj{i}'],'qk':ren[f'qk{i}'],
                        'vj':ren[f'vj{i}'],'vk':ren[f'vk{i}'],'dj':ren[f'dj{i}'],'dk':ren[f'dk{i}'],
                        'imm':dec[i]['imm'],'pc':fetched[f'pc{i}'],'sn':sn})
        lane_rob.append({'op':dec[i]['op'],'rd_new':ren[f'new{i}'],'rd_old':ren[f'old{i}'],
                         'rd_logic':dec[i]['rd'],'rw':dec[i]['rw'],'pc':fetched[f'pc{i}'],
                         'sn':sn,'pred_next':fetched[f'next{i}']})
    stations=[]
    robs=[]
    for u in range(3):
        wr0,wr1=c.ref(f'unit{u}_lane0'),c.ref(f'unit{u}_lane1')
        common={'clk':clk,'rst':rst,'clear':clear,'enable':go,'wr':c.lor(wr0,wr1)}
        srargs={**common,'take':c.ref(f'take{u}')}
        rbargs={**common,'pop':c.ref(f'pop{u}')}
        for field in RS_FIELDS: srargs[field]=c.mux(wr1,lane_rs[0][field],lane_rs[1][field])
        for field in ROB_FIELDS: rbargs[field]=c.mux(wr1,lane_rob[0][field],lane_rob[1][field])
        for k in range(2):
            srargs.update({f'c{k}_valid':c.ref(f'c{k}_valid'),f'c{k}_rd':c.ref(f'c{k}_rd',6),
                           f'c{k}_data':c.ref(f'c{k}_result',16)})
            rbargs.update({f'c{k}_{f}':c.ref(f'c{k}_{f}',w) for f,w in
                           [('valid',1),('sn',8),('result',16),('next',16),('taken',1),('target',16)]})
        stations.append(c.instance(lsrs if u==2 else rs,srargs,f'RS{u}'))
        robs.append(c.instance(rob,rbargs,f'ROB{u}'))
    # Each bank has one physical insertion port. A conflicting second lane is held in fetch.
    capacity=[c.inv(c.lor(stations[u]['full'],robs[u]['full'])) for u in range(3)]
    u0=c.mux(capacity[0],c.const(1,2),c.const(0,2))
    u0=c.mux(dec[0]['mem'],u0,c.const(2,2))
    avail0=c.mux(dec[0]['mem'],c.lor(capacity[0],capacity[1]),capacity[2])
    a0=c.alias('accept0',c.land(go,fetched['valid0'],ren['available0'],avail0))
    remaining=[c.land(capacity[u],c.inv(c.land(a0,c.is_(u0,u)))) for u in range(3)]
    u1=c.mux(remaining[1],c.const(0,2),c.const(1,2))
    u1=c.mux(dec[1]['mem'],u1,c.const(2,2))
    avail1=c.mux(dec[1]['mem'],c.lor(remaining[0],remaining[1]),remaining[2])
    a1=c.alias('accept1',c.land(a0,fetched['valid1'],ren['available1'],avail1))
    for u in range(3):
        c.alias(f'unit{u}_lane0',c.land(a0,c.is_(u0,u)))
        c.alias(f'unit{u}_lane1',c.land(a1,c.is_(u1,u)))
    alu_results=[]
    for u in range(2):
        alu_results.append(c.instance(alu,{f:stations[u]['ex_'+f] for f in ('op','a','b','imm','pc')},f'ALU{u}'))
    take0=c.alias('take0',c.land(go,stations[0]['ex_valid']))
    take1=c.alias('take1',c.land(go,stations[1]['ex_valid'],c.inv(take0)))
    c.alias('c0_valid',c.lor(take0,take1))
    for field,width in [('rd',6),('sn',8)]:
        c.alias('c0_'+field,c.mux(take0,stations[1]['ex_'+field],stations[0]['ex_'+field]))
    for field in ('result','next','taken','target'):
        c.alias('c0_'+field,c.mux(take0,alu_results[1][field],alu_results[0][field]))
    ls=stations[2]
    address=c.add(ls['ex_a'],ls['ex_imm'])
    store=c.is_(ls['ex_op'],9)
    commit_store=c.ref('commit_store')
    stores=c.instance(sb,{'clk':clk,'rst':rst,'enable':go,'clear':clear,
                          'push':c.land(c.ref('take2'),store),'addr':address,'data':ls['ex_b'],
                          'sn':ls['ex_sn'],'load_addr':address,'load_sn':ls['ex_sn'],
                          'commit':commit_store,'commit_sn':retirement_sn},'stores')
    # MMIO reads can have device side effects, so hold them in the LS station
    # until this operation is the oldest instruction. Ordinary RAM loads stay
    # speculative and may execute out of order.
    io_load_addr=c.is_(c.bits(address,8,8),0xf0)
    io_head=c.eq(ls['ex_sn'],retirement_sn)
    io_load=c.land(c.ref('take2'),c.inv(store),io_load_addr,io_head)
    memory=c.instance(mem,{'clk':clk,'rst':rst,'read_addr':address,
                           'read_enable':c.land(c.ref('take2'),c.inv(store)),
                           'io_read_data':io_read_data,'commit':commit_store,
                           'write_addr':stores['commit_addr'],'write_data':stores['commit_data'],
                           'debug_addr':debug_addr},'memory')
    ls_capacity=c.mux(store,c.inv(commit_store),
                      c.land(c.inv(stores['full']),c.lor(c.inv(io_load_addr),io_head)))
    take2=c.alias('take2',c.land(go,ls['ex_valid'],ls_capacity))
    c.alias('c1_valid',take2)
    c.alias('c1_rd',c.mux(store,ls['ex_rd'],c.const(0,6)))
    c.alias('c1_sn',ls['ex_sn'])
    c.alias('c1_result',c.mux(io_load,
                             c.mux(stores['forward'],memory['data'],stores['forward_data']),
                             io_read_data))
    c.alias('c1_next',c.inc(ls['ex_pc']))
    c.alias('c1_target',c.inc(ls['ex_pc']))
    c.alias('c1_taken',c.const(0))
    grants=[]
    for u,h in enumerate(robs):
        grants.append(c.alias(f'pop{u}',c.land(go,h['head_valid'],h['head_done'],
                                              c.eq(h['head_sn'],retirement_sn))))
    c.alias('retire',c.lor(*grants))
    head={}
    for field,width in {**ROB_FIELDS,'result':16,'next':16,'taken':1,'target':16}.items():
        net=c.mux(grants[0],c.mux(grants[1],robs[2]['head_'+field],robs[1]['head_'+field]),robs[0]['head_'+field])
        head[field]=c.alias('retire_'+field,net)
    control_flow=c.lor(*[c.is_(head['op'],k) for k in (10,11,12,13)])
    c.alias('branch_miss',c.land(retire,control_flow,c.ne(head['pred_next'],head['next'])))
    c.alias('bp_we',c.land(retire,control_flow))
    c.alias('halt_commit',c.land(retire,c.is_(head['op'],15)))
    c.alias('commit_store',c.land(retire,c.is_(head['op'],9)))
    c.alias('retire_rd_logic',head['rd_logic'])
    for name,condition in [('cycles',c.land(run,c.inv(halted))),('committed',retire),
                           ('dispatched',go),('dual_issue',a1),('recoveries',branch_miss),
                           ('dual_cdb',c.land(c.ref('c0_valid'),take2))]:
        q=c.ref(name,32)
        d=c.add(q,c.extend(accepted,32)) if name=='dispatched' else c.inc(q)
        c.output(name,c.reg(name,32,d,condition))
    outputs={'halted':halted,'pc':fetched['fetch_pc'],'commit_valid':retire,'commit_sn':retirement_sn,
             'commit_pc':head['pc'],'commit_op':head['op'],'commit_result':head['result'],
             'flush':recovery,'stall':c.land(fetched['valid0'],c.inv(a0)),
             'issue_count':accepted,'cdb0_valid':c.ref('c0_valid'),'cdb1_valid':take2,
             'cdb0_sn':c.ref('c0_sn',8),'cdb1_sn':c.ref('c1_sn',8),
             'fifo_count':fetched['fifo_count'],'store_commit':commit_store,
             'store_addr':stores['commit_addr'],'store_data':stores['commit_data'],
             'forward_hit':c.land(take2,c.inv(store),stores['forward']),
             'sb_count':stores['count'],'memory_debug':memory['debug_data'],
             'io_addr':memory['io_addr'],'io_write_data':memory['io_write_data'],
             'io_read':memory['io_read'],'io_write':memory['io_write'],
             'io_valid':c.lor(memory['io_read'],memory['io_write'])}
    for i in range(8): outputs[f'r{i}']=ren[f'r{i}']
    for u in range(3):
        outputs[f'rs{u}_count']=stations[u]['count']
        outputs[f'rob{u}_count']=robs[u]['count']
    for name,value in outputs.items(): c.output(name,value)
    return c


def build_dashboard(core):
    c=Circuit('Superscalar16','SUPERSCALAR16 | native Logisim circuit | demo program')
    c.text(100,120,'CONTROL')
    reset=c.input('reset',at=(180,180))
    pause=c.input('pause',at=(180,270))
    clk=Net('clock')
    c.comp(0,'Clock',180,360,highDuration=1,lowDuration=1,label='SystemClock')
    c.port(180,360,clk,True)
    run=Net('run_enable')
    c.comp(1,'NOT Gate',330,470,width=1,size=30,facing='east')
    c.port(300,470,pause)
    c.port(330,470,run,True)
    debug_addr=Net('debug_address',16)
    c.comp(0,'Constant',180,580,width=16,value='0x0',facing='east')
    c.port(180,580,debug_addr,True)

    c.text(100,730,'MEMORY-MAPPED I/O  F000-F0FF')
    io_read_data=c.input('io_read_data',16,at=(180,820))

    # Keep this section heading above the CPU instance-label envelope.
    c.text(500,90,'PROCESSOR')
    out=c.instance(core,{'clk':clk,'rst':reset,'run':run,'debug_addr':debug_addr,
                         'io_read_data':io_read_data},
                   'CPU',at=(820,180))

    for i,name in enumerate(('io_addr','io_write_data','io_read','io_write','io_valid')):
        c.output(name,out[name],at=(300,920+i*90))
    c.output('io_clk',clk,at=(300,1400))
    c.output('io_reset',reset,at=(300,1490))

    c.text(1130,120,'ARCHITECTURAL REGISTERS')
    for i in range(8):
        c.output(f'r{i}',out[f'r{i}'],at=(1300,180+i*90))

    c.text(1460,120,'COUNTERS')
    for i,name in enumerate(('cycles','committed','dual_issue','dual_cdb','recoveries')):
        c.output(name,out[name],at=(1630,180+i*90))

    c.text(1460,650,'STATE / MEMORY')
    for i,name in enumerate(('pc','halted','memory_debug')):
        c.output(name,out[name],at=(1630,710+i*90))

    c.text(1130,940,'RESERVATION STATIONS')
    c.text(1460,940,'REORDER BUFFERS')
    for i in range(3):
        c.output(f'rs{i}_count',out[f'rs{i}_count'],at=(1300,1000+i*90))
        c.output(f'rob{i}_count',out[f'rob{i}_count'],at=(1630,1000+i*90))
    c.output('sb_count',out['sb_count'],at=(1300,1270))
    return c
