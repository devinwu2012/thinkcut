"""Deterministic, half-open, source-relative frame intervals."""
from fractions import Fraction

def ms_to_frame(ms, fps=Fraction(30000,1001)):
    value=Fraction(str(ms))*fps/1000
    return (2*value.numerator+value.denominator)//(2*value.denominator)

def kept_segments(total_frames, cuts):
    if type(total_frames) is not int or total_frames < 0:
        raise ValueError('Source frame count must be a non-negative integer')
    cuts=sorted(cuts)
    for a,b in cuts:
        if type(a) is not int or type(b) is not int or not 0<=a<b<=total_frames:
            raise ValueError('Cut outside source or not on integer frames')
    # Merge overlaps only; never swallow unapproved words between cuts.
    merged=[]
    for a,b in cuts:
        if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    result=[];source=0;record=0
    for a,b in merged+[(total_frames,total_frames)]:
        if a>source:
            result.append({'src_in':source,'src_out':a,'tl_in':record,'tl_out':record+a-source});record+=a-source
        source=b
    return result

def source_to_timeline(frame, segments):
    for s in segments:
        if s['src_in']<=frame<s['src_out']:
            return {'frame':s['tl_in']+frame-s['src_in'],'cut':False}
    if not segments: return {'frame':0,'cut':True}
    boundaries=[(s['src_in'],s['tl_in']) for s in segments]+[(s['src_out'],s['tl_out']) for s in segments]
    src,tl=min(boundaries,key=lambda p:(abs(p[0]-frame),p[0]))
    return {'frame':tl,'cut':True}

def timeline_to_source(frame,segments):
    for s in segments:
        if s['tl_in']<=frame<s['tl_out']:return s['src_in']+frame-s['tl_in']
    if segments and frame==segments[-1]['tl_out']:return segments[-1]['src_out']
    raise ValueError('Timeline frame outside range')
