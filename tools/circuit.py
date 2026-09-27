"""Small native Logisim-evolution netlist builder (no external component library)."""
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class Net:
    name: str
    width: int = 1


class Circuit:
    def __init__(self, name, title=None):
        self.name = name
        self.xml = ET.Element('circuit', name=name)
        self.attr(self.xml, 'appearance', 'custom')
        self.inputs = []
        self.outputs = []
        self.n = 0
        self.serial = 0
        self.cache = {}
        self._row_y = 200
        self._row_height = 0
        self._pin_locations = {}
        self.port_pitch = 30
        self.text(100, 40, title or name)

    @staticmethod
    def attr(parent, name, value):
        ET.SubElement(parent, 'a', name=name, val=str(value).lower() if isinstance(value, bool) else str(value))

    def comp(self, lib, name, x, y, **attrs):
        args = {'name': name, 'loc': f'({x},{y})'}
        if lib is not None:
            args['lib'] = str(lib)
        e = ET.SubElement(self.xml, 'comp', **args)
        for k, v in attrs.items():
            self.attr(e, k, v)
        return e

    def text(self, x, y, value):
        self.comp(6, 'Text', x, y, text=value, halign='left', font='SansSerif plain 14')

    def loc(self, height=180):
        i = self.n
        self.n += 1
        if i and i % 4 == 0:
            self._row_y += self._row_height + 100
            self._row_height = 0
        self._row_height = max(self._row_height, height)
        return 1900 + (i % 4) * 1200, self._row_y

    def new(self, width=1, label='n'):
        self.serial += 1
        return Net(f'{label}_{self.serial}', width)

    def ref(self, name, width=1):
        return Net(name, width)

    def wire(self, a, b):
        if a != b:
            ET.SubElement(self.xml, 'wire', **{'from': f'({a[0]},{a[1]})', 'to': f'({b[0]},{b[1]})'})

    def tunnel(self, x, y, net, facing='east'):
        self.comp(0, 'Tunnel', x, y, label=net.name, width=net.width,
                  facing=facing, labelfont='SansSerif plain 12')

    def port(self, x, y, net, output=False, reach=40):
        tx = x + reach if output else x - reach
        self.wire((x, y), (tx, y))
        self.tunnel(tx, y, net, 'west' if output else 'east')

    def ports(self, entries, output=False, pitch=30):
        """Fan close native ports onto separate label rows without crossing wires."""
        entries = sorted(entries, key=lambda p: p[1])
        first, last = entries[0][1], entries[-1][1]
        start = ((first + last - (len(entries) - 1) * pitch) // 20) * 10
        direction = 1 if output else -1
        edge = max(p[0] for p in entries) if output else min(p[0] for p in entries)
        reach = 60 + 10 * ((len(entries) - 1) // 2)
        for i, (x, y, net) in enumerate(entries):
            ty = start + i * pitch
            bend = edge + direction * (20 + 10 * min(i, len(entries) - 1 - i))
            tx = edge + direction * reach
            self.wire((x, y), (bend, y))
            self.wire((bend, y), (bend, ty))
            self.wire((bend, ty), (tx, ty))
            self.tunnel(tx, ty, net, 'west' if output else 'east')

    def input(self, name, width=1, at=None):
        n = Net(name, width)
        x, y = at or (200, 120 + len(self.inputs) * 70)
        self.comp(0, 'Pin', x, y, label=name, width=width, facing='east', type='input',
                  labelloc='north', labelfont='SansSerif plain 12', radix='16' if width > 1 else '2')
        self.port(x, y, n, True)
        self.inputs.append(n)
        self._pin_locations[name, False] = (x, y)
        return n

    def output(self, name, net, at=None):
        n = Net(name, net.width)
        x, y = at or (900, 120 + len(self.outputs) * 70)
        self.comp(0, 'Pin', x, y, label=name, width=net.width, facing='west', type='output',
                  labelloc='north', labelfont='SansSerif plain 12', radix='16' if net.width > 1 else '2')
        self.port(x, y, net)
        self.outputs.append(n)
        self._pin_locations[name, True] = (x, y)
        return n

    def alias(self, name, src):
        n = Net(name, src.width)
        if name != src.name:
            x, y = self.loc()
            self.tunnel(x - 30, y, src)
            self.wire((x-30,y), (x+30,y))
            self.tunnel(x + 30, y, n, 'west')
        return n

    def const(self, value, width=1):
        value &= (1 << width) - 1
        key = ('const', value, width)
        if key not in self.cache:
            n = self.new(width, f'K{value:x}')
            x, y = self.loc()
            self.comp(0, 'Constant', x, y, width=width, value=hex(value), facing='east')
            self.port(x, y, n, True)
            self.cache[key] = n
        return self.cache[key]

    def gate(self, kind, *args):
        if len(args) == 1 and kind != 'NOT':
            return args[0]
        assert args and all(a.width == args[0].width for a in args)
        if len(args) > 2:
            result = args[0]
            for a in args[1:]:
                result = self.gate(kind, result, a)
            return result
        key = (kind, *args)
        if key in self.cache:
            return self.cache[key]
        x, y = self.loc()
        n = self.new(args[0].width, kind.lower())
        attrs = {'width': n.width, 'size': 30, 'facing': 'east'}
        if kind != 'NOT':
            attrs['inputs'] = 2
        self.comp(1, kind + ' Gate', x, y, **attrs)
        if kind == 'NOT':
            self.port(x-30, y, args[0])
        else:
            self.ports([(x-30, y-10, args[0]), (x-30, y+10, args[1])])
        self.port(x,y,n,True)
        self.cache[key] = n
        return n

    def inv(self, a): return self.gate('NOT', a)
    def land(self, *a): return self.gate('AND', *a)
    def lor(self, *a): return self.gate('OR', *a)
    def xor(self, a, b): return self.gate('XOR', a, b)

    def mux(self, sel, a, b):
        assert sel.width == 1 and a.width == b.width
        if a == b:
            return a
        key = ('mux', sel, a, b)
        if key in self.cache:
            return self.cache[key]
        x,y = self.loc()
        n = self.new(a.width, 'mux')
        self.comp(2,'Multiplexer',x,y,width=n.width,select=1,enable=False,size=40)
        self.ports([(x-30,y-10,a),(x-30,y+10,b)])
        self.wire((x-20,y+20),(x-20,y+60))
        self.port(x-20,y+60,sel)
        self.port(x,y,n,True)
        self.cache[key] = n
        return n

    def choose(self, index, data):
        assert len(data) == 1 << index.width
        if len(data) == 1:
            return data[0]
        if len(data) == 2:
            return self.mux(index,data[0],data[1])
        half = len(data)//2
        lo = self.bits(index,0,index.width-1)
        return self.mux(self.bits(index,index.width-1,1), self.choose(lo,data[:half]), self.choose(lo,data[half:]))

    def arithmetic(self, kind, a, b):
        assert a.width == b.width
        x,y = self.loc()
        n = self.new(a.width,kind.lower())
        self.comp(3,kind,x,y,width=a.width)
        self.ports([(x-40,y-10,a),(x-40,y+10,b)])
        self.tunnel(x-20,y-40,self.const(0),'south')
        self.wire((x-20,y-20),(x-20,y-40))
        self.port(x,y,n,True)
        return n

    def add(self,a,b): return self.arithmetic('Adder',a,b)
    def sub(self,a,b): return self.arithmetic('Subtractor',a,b)
    def inc(self,a,k=1): return self.add(a,self.const(k,a.width))

    def compare(self,a,b,which='eq',signed=False):
        assert a.width == b.width
        key = ('cmp',a,b,signed)
        if key not in self.cache:
            x,y=self.loc()
            self.comp(3,'Comparator',x,y,width=a.width,mode='twosComplement' if signed else 'unsigned')
            self.ports([(x-40,y-10,a),(x-40,y+10,b)])
            result={}
            ports=[]
            for field,dy in [('gt',-10),('eq',0),('lt',10)]:
                n=self.new(1,field)
                ports.append((x,y+dy,n))
                result[field]=n
            self.ports(ports,True)
            self.cache[key]=result
        return self.cache[key][which]

    def eq(self,a,b): return self.compare(a,b)
    def is_(self,a,value): return self.eq(a,self.const(value,a.width))
    def ne(self,a,b): return self.inv(self.eq(a,b))
    def nonzero(self,a): return self.inv(self.is_(a,0))

    def bits(self, a, low, width):
        assert width >= 1 and low >= 0 and low+width <= a.width
        if width==a.width:
            return a
        key=('bits',a,low,width)
        if key in self.cache: return self.cache[key]
        x,y=self.loc()
        n=self.new(width,'slice')
        e=self.comp(0,'Splitter',x,y,fanout=1,incoming=a.width,facing='east',appear='right',spacing=1)
        for bit in range(a.width):
            self.attr(e,'bit'+str(bit),0 if low<=bit<low+width else 'none')
        self.port(x,y,a)
        self.port(x+20,y+10,n,True)
        self.cache[key]=n
        return n

    def pack(self, *fields):
        """Fields are low-to-high, each contiguous."""
        assert fields and sum(n.width for n in fields)<=64
        if len(fields)==1: return fields[0]
        x,y=self.loc(height=len(fields)*30+80)
        y += len(fields)*10
        n=self.new(sum(f.width for f in fields),'pack')
        e=self.comp(0,'Splitter',x,y,fanout=len(fields),incoming=n.width,facing='west',appear='left',spacing=1)
        bit=0
        ports=[]
        for i,f in enumerate(fields):
            for _ in range(f.width):
                self.attr(e,'bit'+str(bit),i)
                bit+=1
            ports.append((x-20,y+(i+1)*10,f))
        self.ports(ports)
        self.port(x,y,n,True)
        return n

    def extend(self,a,width,signed=False):
        if a.width==width: return a
        x,y=self.loc()
        n=self.new(width,'ext')
        self.comp(0,'Bit Extender',x,y,**{'in_width':a.width,'out_width':width,'type':'sign' if signed else 'zero'})
        self.port(x-40,y,a)
        self.port(x,y,n,True)
        return n

    def reg(self,name,width,data,en=None,rst=None,init=0):
        q=Net(name,width)
        x,y=self.loc()
        if en is None: en=self.const(1)
        if rst is None: rst=self.ref('rst')
        if init:
            data=self.mux(rst,data,self.const(init,width))
            en=self.lor(en,rst)
            clear=self.const(0)
        else:
            clear=rst
        self.comp(4,'Register',x,y,width=width,appearance='classic',label='reg_'+name,
                  labelfont='SansSerif plain 12')
        self.ports([(x-30,y,data),(x-30,y+10,en)])
        self.wire((x-20,y+20),(x-20,y+60))
        self.port(x-20,y+60,self.ref('clk'))
        self.wire((x-10,y+20),(x-10,y+90))
        self.port(x-10,y+90,clear,True)
        # Register labels are drawn above the body and can extend past the
        # output edge. Leave a readable gap before the named output tunnel.
        self.port(x,y,q,True,80)
        return q

    @property
    def symbol_width(self):
        left=max((len(n.name) for n in self.inputs),default=0)
        right=max((len(n.name) for n in self.outputs),default=0)
        return max(320, ((left+right)*8+100+19)//20*20)

    def instance(self,module,inputs,label=None,at=None):
        h=max(len(module.inputs),len(module.outputs))*module.port_pitch+70
        x,y=at or self.loc(height=h)
        self.comp(None,module.name,x,y,label=label or f'{module.name}_inst{self.n}',
                  labelfont='SansSerif plain 12')
        for i,n in enumerate(module.inputs):
            src=inputs[n.name]
            assert src.width==n.width,(module.name,n,src)
            self.port(x-module.symbol_width,y+i*module.port_pitch,src)
        outs={}
        for i,n in enumerate(module.outputs):
            out=self.new(n.width,(label or module.name)+'_'+n.name)
            self.port(x,y+i*module.port_pitch,out,True)
            outs[n.name]=out
        return outs

    def finish(self):
        appearance=ET.SubElement(self.xml,'appear')
        h=max(len(self.inputs),len(self.outputs))*self.port_pitch+50
        w=self.symbol_width
        ET.SubElement(appearance,'rect',x='50',y='30',width=str(w),height=str(h),fill='#ffffff',stroke='#000000',**{'stroke-width':'2'})
        title=ET.SubElement(appearance,'text',x=str(50+w//2),y='50',fill='#000000',**{'font-family':'SansSerif','font-size':'12','text-anchor':'middle'})
        title.text=self.name
        for output,ports in [(False,self.inputs),(True,self.outputs)]:
            for i,n in enumerate(ports):
                px,py=self._pin_locations[n.name,output]
                ET.SubElement(appearance,'circ-port',x=str(50+w if output else 50),y=str(80+i*self.port_pitch),dir='out' if output else 'in',pin=f'{px},{py}')
                t=ET.SubElement(appearance,'text',x=str(40+w if output else 60),y=str(84+i*self.port_pitch),fill='#000000',**{'font-family':'SansSerif','font-size':'12','text-anchor':'end' if output else 'start'})
                t.text=n.name
        ET.SubElement(appearance,'circ-anchor',x=str(46+w),y='76',width='8',height='8',facing='east')
        return self.xml


def write_project(path,circuits,main='Superscalar16'):
    root=ET.Element('project',source='5.0.0',version='1.0')
    for i,name in enumerate(['Wiring','Gates','Plexers','Arithmetic','Memory','I/O','Base']):
        ET.SubElement(root,'lib',name=str(i),desc='#'+name)
    ET.SubElement(root,'main',name=main)
    options=ET.SubElement(root,'options')
    Circuit.attr(options,'simlimit',100000)
    ET.SubElement(root,'mappings')
    toolbar=ET.SubElement(root,'toolbar')
    ET.SubElement(toolbar,'tool',lib='6',name='Poke Tool')
    ET.SubElement(toolbar,'tool',lib='6',name='Edit Tool')
    ET.SubElement(toolbar,'tool',lib='0',name='Pin')
    for c in circuits: root.append(c.finish())
    ET.indent(root,space='  ')
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
