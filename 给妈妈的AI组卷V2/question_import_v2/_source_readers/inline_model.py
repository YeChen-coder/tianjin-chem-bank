"""Small ordered-inline model shared by formula parsers, independent of UI."""
import copy
def txt(nodes):
    return ''.join(n.get('text','') if n['kind']=='text' else '\n' if n['kind']=='break' else '\t' if n['kind']=='tab' else '\ufffc' for n in nodes)
def slice_nodes(nodes,start,end):
    result=[];pos=0
    for node in nodes:
        size=len(node.get('text','')) if node['kind']=='text' else 1
        a,b=max(0,start-pos),min(size,end-pos)
        if b>a:
            new=copy.deepcopy(node)
            if node['kind']=='text':new['text']=node['text'][a:b]
            result.append(new)
        pos+=size
    return result
def all_nodes(blocks):
    for block in blocks:
        if block['kind']=='paragraph':yield from block['inlines']
        else:
            for row in block.get('rows',[]):
                for cell in row:yield from all_nodes(cell['blocks'])
