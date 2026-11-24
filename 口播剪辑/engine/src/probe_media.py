"""Read MP4 timing tables without re-encoding or decoding 4K video."""
import json, struct, sys
from pathlib import Path
from fractions import Fraction

def atoms(f,start,end):
    pos=start
    while pos+8<=end:
        f.seek(pos); size,kind=struct.unpack('>I4s',f.read(8)); head=8
        if size==1: size=struct.unpack('>Q',f.read(8))[0]; head=16
        if size==0: size=end-pos
        if size<head or pos+size>end: raise ValueError('Invalid atom')
        yield kind.decode('latin1'),pos+head,pos+size
        pos+=size

def children(f,node): return list(atoms(f,node[1],node[2]))
def one(nodes,kind): return next(n for n in nodes if n[0]==kind)
def data(f,node): f.seek(node[1]); return f.read(node[2]-node[1])
def timing(raw):
    offset=20 if raw[0] else 12
    scale=struct.unpack_from('>I',raw,offset)[0]
    duration=struct.unpack_from('>Q' if raw[0] else '>I',raw,offset+4)[0]
    return scale,duration

def probe(path):
    with path.open('rb') as f:
        moov=children(f,one(list(atoms(f,0,path.stat().st_size)),'moov'))
        movie_scale,movie_duration=timing(data(f,one(moov,'mvhd')))
        tracks=[]
        for track in [x for x in moov if x[0]=='trak']:
            tk=children(f,track); md=children(f,one(tk,'mdia'))
            handler=data(f,one(md,'hdlr'))[8:12].decode()
            scale,duration=timing(data(f,one(md,'mdhd')))
            stbl=children(f,one(children(f,one(md,'minf')),'stbl'))
            raw=data(f,one(stbl,'stts')); n=struct.unpack_from('>I',raw,4)[0]
            runs=[struct.unpack_from('>II',raw,8+i*8) for i in range(n)]
            edits=[]
            edts=next((x for x in tk if x[0]=='edts'),None)
            if edts:
                raw=data(f,one(children(f,edts),'elst')); n=struct.unpack_from('>I',raw,4)[0]
                fmt='>Qqhh' if raw[0] else '>Iihh'; length=struct.calcsize(fmt)
                edits=[dict(zip(['movie_duration','media_time','rate_integer','rate_fraction'],struct.unpack_from(fmt,raw,8+i*length))) for i in range(n)]
            row={'handler':handler,'timescale':scale,'duration_units':duration,'duration_seconds':duration/scale,'sample_count':sum(r[0] for r in runs),'stts_runs':runs,'edits':edits}
            if handler=='vide':
                # tkhd: stored size (16.16) and display matrix; a quarter turn swaps the displayed size.
                raw=data(f,one(tk,'tkhd')); base=52 if raw[0] else 40
                a,b,_,c,d=struct.unpack_from('>iiiii',raw,base)
                width,height=(struct.unpack_from('>I',raw,base+36+i)[0]>>16 for i in (0,4))
                row['width'],row['height']=width,height
                row['display_size']=[height,width] if a==0 and d==0 and b and c else [width,height]
                deltas=set(r[1] for r in runs); row['constant_decode_duration']=len(deltas)==1
                if len(deltas)==1:
                    fps=Fraction(scale,next(iter(deltas)));row['fps']={'num':fps.numerator,'den':fps.denominator}
                ctts=next((x for x in stbl if x[0]=='ctts'),None)
                dts=[];t=0
                for count,delta in runs:
                    dts.extend(range(t,t+count*delta,delta));t+=count*delta
                if ctts:
                    raw=data(f,ctts);n=struct.unpack_from('>I',raw,4)[0];offsets=[]
                    for i in range(n):
                        count,offset=struct.unpack_from('>Ii' if raw[0] else '>II',raw,8+8*i);offsets.extend([offset]*count)
                    assert len(dts)==len(offsets)
                    pts=sorted(a+b for a,b in zip(dts,offsets))
                else: pts=dts
                differences=sorted(set(b-a for a,b in zip(pts,pts[1:])))
                row['presentation_deltas']=differences;row['constant_presentation_duration']=len(differences)==1
            tracks.append(row)
    return {'path':str(path),'size_bytes':path.stat().st_size,'movie_timescale':movie_scale,'movie_duration_seconds':movie_duration/movie_scale,'tracks':tracks}
if __name__=='__main__':
    result=probe(Path(sys.argv[1]));Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
