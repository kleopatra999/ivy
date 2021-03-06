#! /usr/bin/env python
#
# Copyright (c) Microsoft Corporation. All Rights Reserved.
#

import ivy
import ivy_logic as il
import ivy_module as im
import ivy_utils as iu
import ivy_actions as ia
import logic as lg
import logic_util as lu
import ivy_solver as slv
import ivy_transrel as tr
import ivy_logic_utils as ilu
import ivy_compiler as ic
import ivy_isolate as iso
import ivy_ast
import itertools

from collections import defaultdict

def all_state_symbols():
    return [s for s in il.all_symbols() if s not in il.sig.constructors]

def sort_card(sort):
    if hasattr(sort,'card'):
        return sort.card
    if sort.is_relational():
        return 2
    return slv.sort_card(sort)
    if hasattr(sort,'name'):
        name = sort.name
        if name in il.sig.interp:
            sort = il.sig.interp[name]
            if isinstance(sort,il.EnumeratedSort):
                return sort.card
            card = slv.sort_card(sort)
            if card != None:
                return card
    raise iu.IvyError(None,'sort {} has no finite interpretation'.format(sort))
    
indent_level = 0

def indent(header):
    header.append(indent_level * '    ')

def get_indent(line):
    lindent = 0
    for char in line:
        if char == ' ':
            lindent += 1
        elif char == '\t':
            lindent = (lindent + 8) / 8 * 8
        else:
            break
    return lindent

def indent_code(header,code):
    code = code.rstrip() # remove trailing whitespace
    indent = min(get_indent(line) for line in code.split('\n') if line.strip() != "")
    for line in code.split('\n'):
        header.append((indent_level * 4 + get_indent(line) - indent) * ' ' + line.strip() + '\n')

def declare_symbol(header,sym,c_type = None):
    if slv.solver_name(sym) == None:
        return # skip interpreted symbols
    name, sort = sym.name,sym.sort
    if not c_type:
        c_type = 'bool' if sort.is_relational() else 'int'
    header.append('    ' + c_type + ' ')
    header.append(varname(sym.name))
    if hasattr(sort,'dom'):
        for d in sort.dom:
            header.append('[' + str(sort_card(d)) + ']')
    header.append(';\n')

special_names = {
    '<' : '__lt',
    '<=' : '__le',
    '>' : '__gt',
    '>=' : '__ge',
}

def varname(name):
    global special_names
    if not isinstance(name,str):
        name = name.name
    if name in special_names:
        return special_names[name]
    name = name.replace('loc:','loc__').replace('ext:','ext__').replace('___branch:','__branch__').replace('.','__')
    return name.split(':')[-1]

def mk_nondet(code,v,rng,name,unique_id):
    global nondet_cnt
    indent(code)
    code.append(varname(v) + ' = ___ivy_choose(' + str(rng) + ',"' + name + '",' + str(unique_id) + ');\n')

def emit_sorts(header):
    for name,sort in il.sig.sorts.iteritems():
        if name == "bool":
            continue
        if name in il.sig.interp:
            sort = il.sig.interp[name]
        if not isinstance(sort,il.EnumeratedSort):
            sortname = str(sort)
#            print "sortname: {}".format(sortname)
            if sortname.startswith('bv[') and sortname.endswith(']'):
                width = int(sortname[3:-1])
                indent(header)
                header.append('mk_bv("{}",{});\n'.format(name,width))
                continue
            raise iu.IvyError(None,'sort {} has no finite interpretation'.format(name))
        card = sort.card
        cname = varname(name)
        indent(header)
        header.append("const char *{}_values[{}]".format(cname,card) +
                      " = {" + ','.join('"{}"'.format(x) for x in sort.extension) + "};\n");
        indent(header)
        header.append('mk_enum("{}",{},{}_values);\n'.format(name,card,cname))

def emit_decl(header,symbol):
    name = symbol.name
    sname = slv.solver_name(symbol)
    if sname == None:  # this means the symbol is interpreted in some theory
        return 
    cname = varname(name)
    sort = symbol.sort
    rng_name = "Bool" if sort.is_relational() else sort.rng.name
    domain = sort_domain(sort)
    if len(domain) == 0:
        indent(header)
        header.append('mk_const("{}","{}");\n'.format(sname,rng_name))
    else:
        card = len(domain)
        indent(header)
        header.append("const char *{}_domain[{}]".format(cname,card) + " = {"
                      + ','.join('"{}"'.format(s.name) for s in domain) + "};\n");
        indent(header)
        header.append('mk_decl("{}",{},{}_domain,"{}");\n'.format(sname,card,cname,rng_name))
        
def emit_sig(header):
    emit_sorts(header)
    for symbol in all_state_symbols():
        emit_decl(header,symbol)

def sort_domain(sort):
    if hasattr(sort,"domain"):
        return sort.domain
    return []

def emit_eval(header,symbol,obj=None): 
    global indent_level
    name = symbol.name
    sname = slv.solver_name(symbol)
    cname = varname(name)
    sort = symbol.sort
    domain = sort_domain(sort)
    for idx,dsort in enumerate(domain):
        dcard = sort_card(dsort)
        indent(header)
        header.append("for (int X{} = 0; X{} < {}; X{}++)\n".format(idx,idx,dcard,idx))
        indent_level += 1
    indent(header)
    header.append((obj + '.' if obj else '')
                  + cname + ''.join("[X{}]".format(idx) for idx in range(len(domain)))
                  + ' = eval_apply("{}"'.format(sname)
                  + ''.join(",X{}".format(idx) for idx in range(len(domain)))
                  + ");\n")
    for idx,dsort in enumerate(domain):
        indent_level -= 1    

def emit_set(header,symbol): 
    global indent_level
    name = symbol.name
    sname = slv.solver_name(symbol)
    cname = varname(name)
    sort = symbol.sort
    domain = sort_domain(sort)
    for idx,dsort in enumerate(domain):
        dcard = sort_card(dsort)
        indent(header)
        header.append("for (int X{} = 0; X{} < {}; X{}++)\n".format(idx,idx,dcard,idx))
        indent_level += 1
    indent(header)
    header.append('set("{}"'.format(sname)
                  + ''.join(",X{}".format(idx) for idx in range(len(domain)))
                  + ",obj.{}".format(cname)+ ''.join("[X{}]".format(idx) for idx in range(len(domain)))
                  + ");\n")
    for idx,dsort in enumerate(domain):
        indent_level -= 1    

def emit_eval_sig(header,obj=None):
    for symbol in all_state_symbols():
        if slv.solver_name(symbol) != None: # skip interpreted symbols
            global is_derived
            if symbol not in is_derived:
                emit_eval(header,symbol,obj)

def emit_clear_progress(impl,obj=None):
    for df in im.module.progress:
        vs = list(lu.free_variables(df.args[0]))
        open_loop(impl,vs)
        code = []
        indent(code)
        if obj != None:
            code.append('obj.')
        df.args[0].emit(impl,code)
        code.append(' = 0;\n')
        impl.extend(code)
        close_loop(impl,vs)

def emit_init_gen(header,impl,classname):
    global indent_level
    header.append("""
class init_gen : public gen {
public:
    init_gen();
""")
    impl.append("init_gen::init_gen(){\n");
    indent_level += 1
    emit_sig(impl)
    indent(impl)
    impl.append('add("(assert (and\\\n')
    constraints = [im.module.init_cond.to_formula()]
    for a in im.module.axioms:
        constraints.append(a)
    for df in im.module.concepts:
        constraints.append(df.to_constraint())
    for c in constraints:
        fmla = slv.formula_to_z3(c).sexpr().replace('\n',' ')
        indent(impl)
        impl.append("  {}\\\n".format(fmla))
    indent(impl)
    impl.append('))");\n')
    indent_level -= 1
    impl.append("}\n");
    header.append("    bool generate(" + classname + "&);\n};\n")
    impl.append("bool init_gen::generate(" + classname + "& obj) {\n")
    indent_level += 1
    for sym in all_state_symbols():
        if slv.solver_name(sym) != None: # skip interpreted symbols
            global is_derived
            if sym not in is_derived:
                emit_randomize(impl,sym)
    indent_level -= 1
    impl.append("""
    bool res = solve();
    if (res) {
""")
    indent_level += 2
    emit_eval_sig(impl,'obj')
    emit_clear_progress(impl,'obj')
    indent_level -= 2
    impl.append("""
    }
    return res;
}
""")
    
def emit_randomize(header,symbol):

    global indent_level
    name = symbol.name
    sname = slv.solver_name(symbol)
    cname = varname(name)
    sort = symbol.sort
    domain = sort_domain(sort)
    for idx,dsort in enumerate(domain):
        dcard = sort_card(dsort)
        indent(header)
        header.append("for (int X{} = 0; X{} < {}; X{}++)\n".format(idx,idx,dcard,idx))
        indent_level += 1
    indent(header)
    header.append('randomize("{}"'.format(sname)
                  + ''.join(",X{}".format(idx) for idx in range(len(domain)))
                  + ");\n")
    for idx,dsort in enumerate(domain):
        indent_level -= 1    

#    indent(header)
#    header.append('randomize("{}");\n'.format(slv.solver_name(symbol)))

def emit_action_gen(header,impl,name,action,classname):
    global indent_level
    caname = varname(name)
    upd = action.update(im.module,None)
    pre = tr.reverse_image(ilu.true_clauses(),ilu.true_clauses(),upd)
    pre_clauses = ilu.trim_clauses(pre)
    pre_clauses = ilu.and_clauses(pre_clauses,ilu.Clauses([df.to_constraint() for df in im.module.concepts]))
    pre = pre_clauses.to_formula()
    syms = [x for x in ilu.used_symbols_ast(pre) if x.name not in il.sig.symbols]
    header.append("class " + caname + "_gen : public gen {\n  public:\n")
    for sym in syms:
        if not sym.name.startswith('__ts') and sym not in pre_clauses.defidx:
            declare_symbol(header,sym)
    header.append("    {}_gen();\n".format(caname))
    impl.append(caname + "_gen::" + caname + "_gen(){\n");
    indent_level += 1
    emit_sig(impl)
    for sym in syms:
        emit_decl(impl,sym)
    
    indent(impl)
    impl.append('add("(assert {})");\n'.format(slv.formula_to_z3(pre).sexpr().replace('\n','\\\n')))
    indent_level -= 1
    impl.append("}\n");
    header.append("    bool generate(" + classname + "&);\n};\n");
    impl.append("bool " + caname + "_gen::generate(" + classname + "& obj) {\n    push();\n")
    indent_level += 1
    pre_used = ilu.used_symbols_ast(pre)
    for sym in all_state_symbols():
        if sym in pre_used and sym not in pre_clauses.defidx: # skip symbols not used in constraint
            if slv.solver_name(sym) != None: # skip interpreted symbols
                global is_derived
                if sym not in is_derived:
                    emit_set(impl,sym)
    for sym in syms:
        if not sym.name.startswith('__ts') and sym not in pre_clauses.defidx:
            emit_randomize(impl,sym)
    impl.append("""
    bool res = solve();
    if (res) {
""")
    indent_level += 1
    for sym in syms:
        if not sym.name.startswith('__ts') and sym not in pre_clauses.defidx:
            emit_eval(impl,sym)
    indent_level -= 2
    impl.append("""
    }
    pop();
    obj.___ivy_gen = this;
    return res;
}
""")

def emit_action_gen(header,impl,name,action,classname):
    global indent_level
    caname = varname(name)
    upd = action.update(im.module,None)
    pre = tr.reverse_image(ilu.true_clauses(),ilu.true_clauses(),upd)
    pre_clauses = ilu.trim_clauses(pre)
    pre_clauses = ilu.and_clauses(pre_clauses,ilu.Clauses([df.to_constraint() for df in im.module.concepts]))
    pre = pre_clauses.to_formula()
    syms = [x for x in ilu.used_symbols_ast(pre) if x.name not in il.sig.symbols]
    header.append("class " + caname + "_gen : public gen {\n  public:\n")
    for sym in syms:
        if not sym.name.startswith('__ts') and sym not in pre_clauses.defidx:
            declare_symbol(header,sym)
    header.append("    {}_gen();\n".format(caname))
    impl.append(caname + "_gen::" + caname + "_gen(){\n");
    indent_level += 1
    emit_sig(impl)
    for sym in syms:
        emit_decl(impl,sym)
    
    indent(impl)
    impl.append('add("(assert {})");\n'.format(slv.formula_to_z3(pre).sexpr().replace('\n','\\\n')))
    indent_level -= 1
    impl.append("}\n");
    header.append("    bool generate(" + classname + "&);\n};\n");
    impl.append("bool " + caname + "_gen::generate(" + classname + "& obj) {\n    push();\n")
    indent_level += 1
    pre_used = ilu.used_symbols_ast(pre)
    for sym in all_state_symbols():
        if sym in pre_used and sym not in pre_clauses.defidx: # skip symbols not used in constraint
            if slv.solver_name(sym) != None: # skip interpreted symbols
                global is_derived
                if sym not in is_derived:
                    emit_set(impl,sym)
    for sym in syms:
        if not sym.name.startswith('__ts') and sym not in pre_clauses.defidx:
            emit_randomize(impl,sym)
    impl.append("""
    bool res = solve();
    if (res) {
""")
    indent_level += 1
    for sym in syms:
        if not sym.name.startswith('__ts') and sym not in pre_clauses.defidx:
            emit_eval(impl,sym)
    indent_level -= 2
    impl.append("""
    }
    pop();
    obj.___ivy_gen = this;
    return res;
}
""")
def emit_derived(header,impl,df,classname):
    name = df.defines().name
    sort = df.defines().sort
    retval = il.Symbol("ret:val",sort)
    vs = df.args[0].args
    ps = [ilu.var_to_skolem('p:',v) for v in vs]
    mp = dict(zip(vs,ps))
    rhs = ilu.substitute_ast(df.args[1],mp)
    action = ia.AssignAction(retval,rhs)
    action.formal_params = ps
    action.formal_returns = [retval]
    emit_some_action(header,impl,name,action,classname)


def native_split(string):
    split = string.split('\n',1)
    if len(split) == 2:
        tag = split[0].strip()
        return ("member" if not tag else tag),split[1]
    return "member",split[0]

def native_type(native):
    tag,code = native_split(native.args[1].code)
    return tag

def native_declaration(atom):
    res = varname(atom.rep)
    for arg in atom.args:
        sort = arg.sort if isinstance(arg.sort,str) else arg.sort.name
        res += '[' + str(sort_card(im.module.sig.sorts[sort])) + ']'
    return res

thunk_counter = 0

def action_return_type(action):
    return 'int' if action.formal_returns else 'void'

def thunk_name(actname):
    return 'thunk__' + varname(actname)

def create_thunk(impl,actname,action,classname):
    tc = thunk_name(actname)
    impl.append('struct ' + tc + '{\n')
    impl.append('    ' + classname + ' *__ivy' + ';\n')
    
    params = [p for p in action.formal_params if p.name.startswith('prm:')]
    inputs = [p for p in action.formal_params if not p.name.startswith('prm:')]
    for p in params:
        declare_symbol(impl,p)
    impl.append('    ')
    emit_param_decls(impl,tc,params,extra = [ classname + ' *__ivy'])
    impl.append(': __ivy(__ivy)' + ''.join(',' + varname(p) + '(' + varname(p) + ')' for p in params) + '{}\n')
    impl.append('    ' + action_return_type(action) + ' ')
    emit_param_decls(impl,'operator()',inputs);
    impl.append(' const {\n        __ivy->' + varname(actname)
                + '(' + ','.join(varname(p.name) for p in action.formal_params) + ');\n    }\n};\n')

def native_typeof(arg):
    if isinstance(arg,ivy_ast.Atom):
        if arg.rep in im.module.actions:
            return thunk_name(arg.rep)
        raise iu.IvyError(arg,'undefined action: ' + arg.rep)
    return int + len(arg.sort.dom) * '[]'

def native_to_str(native,reference=False):
    tag,code = native_split(native.args[1].code)
    fields = code.split('`')
    f = native_reference if reference else native_declaration
    def nfun(idx):
        return native_typeof if fields[idx-1].endswith('%') else f
    def dm(s):
        return s[:-1] if s.endswith('%') else s
    fields = [(nfun(idx)(native.args[int(s)+2]) if idx % 2 == 1 else dm(s)) for idx,s in enumerate(fields)]
    return ''.join(fields)

def emit_native(header,impl,native,classname):
    header.append(native_to_str(native))

def emit_param_decls(header,name,params,extra=[]):
    header.append(varname(name) + '(')
    header.append(', '.join(extra + ['int ' + varname(p.name) for p in params]))
    header.append(')')

def emit_method_decl(header,name,action,body=False,classname=None):
    if not hasattr(action,"formal_returns"):
        print "bad name: {}".format(name)
        print "bad action: {}".format(action)
    rs = action.formal_returns
    if not body:
        header.append('    ')
    if not body and target.get() != "gen":
        header.append('virtual ')
    if len(rs) == 0:
        header.append('void ')
    elif len(rs) == 1:
        header.append('int ')
    else:
        raise iu.IvyError(action,'cannot handle multiple output values')
    if body:
        header.append(classname + '::')
    emit_param_decls(header,name,action.formal_params)
    
def emit_action(header,impl,name,classname):
    action = im.module.actions[name]
    emit_some_action(header,impl,name,action,classname)

def emit_some_action(header,impl,name,action,classname):
    global indent_level
    emit_method_decl(header,name,action)
    header.append(';\n')
    emit_method_decl(impl,name,action,body=True,classname=classname)
    impl.append('{\n')
    indent_level += 1
    if len(action.formal_returns) == 1:
        indent(impl)
        impl.append('int ' + varname(action.formal_returns[0].name) + ';\n')
    action.emit(impl)
    if len(action.formal_returns) == 1:
        indent(impl)
        impl.append('return ' + varname(action.formal_returns[0].name) + ';\n')
    indent_level -= 1
    impl.append('}\n')

def init_method():
    asserts = [ia.AssertAction(im.module.init_cond.to_formula())]
    for a in im.module.axioms:
        asserts.append(ia.AssertAction(a))
    res = ia.Sequence(*asserts)
    res.formal_params = []
    res.formal_returns = []
    return res

def open_loop(impl,vs,declare=True):
    global indent_level
    for idx in vs:
        indent(impl)
        impl.append('for ('+ ('int ' if declare else '') + idx.name + ' = 0; ' + idx.name + ' < ' + str(sort_card(idx.sort)) + '; ' + idx.name + '++) {\n')
        indent_level += 1

def close_loop(impl,vs):
    global indent_level
    for idx in vs:
        indent_level -= 1    
        indent(impl)
        impl.append('}\n')
        
def open_scope(impl,newline=False,line=None):
    global indent_level
    if line != None:
        indent(impl)
        impl.append(line)
    if newline:
        impl.append('\n')
        indent(impl)
    impl.append('{\n')
    indent_level += 1

def open_if(impl,cond):
    open_scope(impl,line='if('+(''.join(cond) if isinstance(cond,list) else cond)+')')
    
def close_scope(impl):
    global indent_level
    indent_level -= 1
    indent(impl)
    impl.append('}\n')

# This generates the "tick" method, called by the test environment to
# represent passage of time. For each progress property, if it is not
# satisfied the counter is incremented else it is set to zero. For each
# property the maximum of the counter values for all its relies is
# computed and the test environment's ivy_check_progress function is called.

# This is currently a bit bogus, since we could miss satisfaction of
# the progress property occurring between ticks.

def emit_tick(header,impl,classname):
    global indent_level
    indent_level += 1
    indent(header)
    header.append('void __tick(int timeout);\n')
    indent_level -= 1
    indent(impl)
    impl.append('void ' + classname + '::__tick(int __timeout){\n')
    indent_level += 1

    rely_map = defaultdict(list)
    for df in im.module.rely:
        key = df.args[0] if isinstance(df,il.Implies) else df
        rely_map[key.rep].append(df)

    for df in im.module.progress:
        vs = list(lu.free_variables(df.args[0]))
        open_loop(impl,vs)
        code = []
        indent(code)
        df.args[0].emit(impl,code)
        code.append(' = ')
        df.args[1].emit(impl,code)
        code.append(' ? 0 : ')
        df.args[0].emit(impl,code)
        code.append(' + 1;\n')
        impl.extend(code)
        close_loop(impl,vs)


    for df in im.module.progress:
        if any(not isinstance(r,il.Implies) for r in rely_map[df.defines()]):
            continue
        vs = list(lu.free_variables(df.args[0]))
        open_loop(impl,vs)
        maxt = new_temp(impl)
        indent(impl)
        impl.append(maxt + ' = 0;\n') 
        for r in rely_map[df.defines()]:
            if not isinstance(r,il.Implies):
                continue
            rvs = list(lu.free_variables(r.args[0]))
            assert len(rvs) == len(vs)
            subs = dict(zip(rvs,vs))

            ## TRICKY: If there are any free variables on rhs of
            ## rely not occuring on left, we must prevent their capture
            ## by substitution

            xvs = set(lu.free_variables(r.args[1]))
            xvs = xvs - set(rvs)
            for xv in xvs:
                subs[xv.name] = xv.rename(xv.name + '__')
            xvs = [subs[xv.name] for xv in xvs]
    
            e = ilu.substitute_ast(r.args[1],subs)
            open_loop(impl,xvs)
            indent(impl)
            impl.append('{} = std::max({},'.format(maxt,maxt))
            e.emit(impl,impl)
            impl.append(');\n')
            close_loop(impl,xvs)
        indent(impl)
        impl.append('if (' + maxt + ' > __timeout)\n    ')
        indent(impl)
        df.args[0].emit(impl,impl)
        impl.append(' = 0;\n')
        indent(impl)
        impl.append('ivy_check_progress(')
        df.args[0].emit(impl,impl)
        impl.append(',{});\n'.format(maxt))
        close_loop(impl,vs)

    indent_level -= 1
    indent(impl)
    impl.append('}\n')

def module_to_cpp_class(classname):
    global is_derived
    is_derived = set()
    for df in im.module.concepts:
        is_derived.add(df.defines())

    # remove the actions not reachable from exported
        
# TODO: may want to call internal actions from testbench

#    ra = iu.reachable(im.module.public_actions,lambda name: im.module.actions[name].iter_calls())
#    im.module.actions = dict((name,act) for name,act in im.module.actions.iteritems() if name in ra)

    header = []
    if target.get() == "gen":
        header.append('extern void ivy_assert(bool,const char *);\n')
        header.append('extern void ivy_assume(bool,const char *);\n')
        header.append('extern void ivy_check_progress(int,int);\n')
        header.append('extern int choose(int,int);\n')
        header.append('struct ivy_gen {virtual int choose(int rng,const char *name) = 0;};\n')
    header.append('#include <vector>\n')

    once_memo = set()
    for native in im.module.natives:
        tag = native_type(native)
        if tag == "header":
            code = native_to_str(native)
            if code not in once_memo:
                once_memo.add(code)
                header.append(code)


    header.append('class ' + classname + ' {\n  public:\n')
    header.append('    std::vector<int> ___ivy_stack;\n')
    if target.get() == "gen":
        header.append('    ivy_gen *___ivy_gen;\n')
    header.append('    int ___ivy_choose(int rng,const char *name,int id);\n')
    if target.get() != "gen":
        header.append('    virtual void ivy_assert(bool,const char *){}\n')
        header.append('    virtual void ivy_assume(bool,const char *){}\n')
        header.append('    virtual void ivy_check_progress(int,int){}\n')
    
    impl = ['#include "' + classname + '.h"\n\n']
    impl.append("#include <sstream>\n")
    impl.append("#include <algorithm>\n")
    impl.append("""
#include <iostream>
#include <stdlib.h>
#include <sys/types.h>          /* See NOTES */
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/ip.h> 
#include <sys/select.h>
#include <string.h>
#include <stdio.h>
#include <string>
#include <unistd.h>
""")
    impl.append("typedef {} ivy_class;\n".format(classname))

    native_exprs = []
    for n in im.module.natives:
        native_exprs.extend(n.args[2:])
    for n in im.module.actions.values():
        if isinstance(n,ia.NativeAction):
            native_exprs.extend(n.args[1:])
    callbacks = set()
    for e in native_exprs:
        if isinstance(e,ivy_ast.Atom) and e.rep in im.module.actions:
            callbacks.add(e.rep)
    for actname in sorted(callbacks):
        action = im.module.actions[actname]
        create_thunk(impl,actname,action,classname)

    impl.append("""
class reader {
public:
    virtual int fdes() = 0;
    virtual void read() = 0;
};
void install_reader(reader *);
class timer {
public:
    virtual int ms_delay() = 0;
    virtual void timeout() = 0;
};
void install_timer(timer *);
""")

    once_memo = set()
    for native in im.module.natives:
        tag = native_type(native)
        if tag == "impl":
            code = native_to_str(native)
            if code not in once_memo:
                once_memo.add(code)
                impl.append(code)


    impl.append("int " + classname)
    if target.get() == "gen":
        impl.append(
"""::___ivy_choose(int rng,const char *name,int id) {
        std::ostringstream ss;
        ss << name << ':' << id;;
        for (unsigned i = 0; i < ___ivy_stack.size(); i++)
            ss << ':' << ___ivy_stack[i];
        return ___ivy_gen->choose(rng,ss.str().c_str());
    }
""")
    else:
        impl.append(
"""::___ivy_choose(int rng,const char *name,int id) {
        return 0;
    }
""")
    for sym in all_state_symbols():
        if sym not in is_derived:
            declare_symbol(header,sym)
    for sym in il.sig.constructors:
        declare_symbol(header,sym)
    for sname in il.sig.interp:
        header.append('    int __CARD__' + varname(sname) + ';\n')
    for df in im.module.concepts:
        emit_derived(header,impl,df,classname)

    for native in im.module.natives:
        tag = native_type(native)
        if tag not in ["member","init","header","impl"]:
            raise iu.IvyError(native,"syntax error at token {}".format(tag))
        if tag == "member":
            emit_native(header,impl,native,classname)

    # declare one counter for each progress obligation
    # TRICKY: these symbols are boolean but we create a C++ int
    for df in im.module.progress:
        declare_symbol(header,df.args[0].rep,c_type = 'int')

    header.append('    ');
    emit_param_decls(header,classname,im.module.params)
    header.append(';\n');
    im.module.actions['.init'] = init_method()
    for a in im.module.actions:
        emit_action(header,impl,a,classname)
    emit_tick(header,impl,classname)
    header.append('};\n')

    impl.append(classname + '::')
    emit_param_decls(impl,classname,im.module.params)
    impl.append('{\n')
    enums = set(sym.sort.name for sym in il.sig.constructors)  
    for sortname in enums:
        for i,n in enumerate(il.sig.sorts[sortname].extension):
            impl.append('    {} = {};\n'.format(varname(n),i))
    for sortname in il.sig.interp:
        if sortname in il.sig.sorts:
            impl.append('    __CARD__{} = {};\n'.format(varname(sortname),sort_card(il.sig.sorts[sortname])))
    if target.get() != "gen":
        emit_one_initial_state(impl)
    for native in im.module.natives:
        tag = native_type(native)
        if tag == "init":
            vs = [il.Symbol(v.rep,im.module.sig.sorts[v.sort]) for v in native.args[0].args]
            global indent_level
            indent_level += 1
            open_loop(impl,vs)
            code = native_to_str(native,reference=True)
            indent_code(impl,code)
            close_loop(impl,vs)
            indent_level -= 1

    impl.append('}\n')

    if target.get() == "gen":
        emit_boilerplate1(header,impl)
        emit_init_gen(header,impl,classname)
        for name,action in im.module.actions.iteritems():
            if name in im.module.public_actions:
                emit_action_gen(header,impl,name,action,classname)

    if target.get() == "repl":
        def csortcard(s):
            card = sort_card(s)
            return str(card) if card else "0"
        emit_repl_imports(header,impl,classname)
        emit_repl_boilerplate1(header,impl,classname)
        for actname in sorted(im.module.public_actions):
            username = actname[4:] if actname.startswith("ext:") else actname
            action = im.module.actions[actname]
            getargs = ','.join('int_arg(args,{},{})'.format(idx,csortcard(x.sort)) for idx,x in enumerate(action.formal_params))
            thing = "ivy.methodname(getargs)"
            if action.formal_returns:
                thing = "std::cout << " + thing + " << std::endl"
            impl.append("""
            if (action == "actname") {
                check_arity(args,numargs,action);
                thing;
            }
            else
""".replace('thing',thing).replace('actname',username).replace('methodname',varname(actname)).replace('numargs',str(len(action.formal_params))).replace('getargs',getargs))
        emit_repl_boilerplate2(header,impl,classname)
        impl.append("    if (argc != "+str(len(im.module.params)+1)+"){\n")
        impl.append('        std::cerr << "usage: {} {}\\n";\n'
                    .format(classname,' '.join(map(varname,im.module.params))))
        impl.append('        exit(1);\n    }\n')
        impl.append('    std::vector<std::string> args;\n')
        impl.append('    for(int i = 1; i < argc;i++){args.push_back(argv[i]);}\n')
        for idx,s in enumerate(im.module.params):
            impl.append('    int p__'+varname(s)+';\n')
            impl.append('    try {\n')
            impl.append('        p__'+varname(s)+' =  int_arg(args,{},{});\n'.format(idx,csortcard(s.sort)))
            impl.append('    }\n    catch(out_of_bounds &) {\n')
            impl.append('        std::cerr << "parameter {} out of bounds\\n";\n'.format(varname(s)))
            impl.append('        exit(1);\n    }\n')
        cp = '(' + ','.join('p__'+varname(s) for s in im.module.params) + ')' if im.module.params else ''
        impl.append('    {}_repl ivy{};\n'
                    .format(classname,cp))
        emit_repl_boilerplate3(header,impl,classname)

        
    return ''.join(header) , ''.join(impl)


def check_representable(sym,ast=None):
    sort = sym.sort
    if hasattr(sort,'dom'):
        for domsort in sort.dom:
            card = sort_card(domsort)
            if card == None:
                raise iu.IvyError(ast,'cannot compile "{}" because type {} is uninterpreted'.format(sym,domsort))
            if card > 16:
                raise iu.IvyError(ast,'cannot compile "{}" because type {} is large'.format(sym,domsort))

cstr = il.fmla_to_str_ambiguous

def assign_symbol_from_model(header,sym,m):
    if slv.solver_name(sym) == None:
        return # skip interpreted symbols
    name, sort = sym.name,sym.sort
    check_representable(sym)
    if hasattr(sort,'dom'):
        for args in itertools.product(*[range(sort_card(s)) for s in sym.sort.dom]):
            term = sym(*[il.Symbol(str(a),s) for a,s in zip(args,sym.sort.dom)])
            val = m.eval_to_constant(term)
            header.append(varname(sym.name) + ''.join('['+str(a)+']' for a in args) + ' = ')
            header.append(cstr(val) + ';\n')
    else:
        header.append(varname(sym.name) + ' = ' + cstr(m.eval_to_constant(sym)) + ';\n')
        
def check_init_cond(kind,lfmlas):
    params = set(im.module.params)
    for lfmla in lfmlas:
        if any(c in params for c in ilu.used_symbols_ast(lfmla.formula)):
            raise iu.IvyError(lfmla,"{} depends on stripped parameter".format(kind))
        
    
def emit_one_initial_state(header):
    check_init_cond("initial condition",im.module.labeled_inits)
    check_init_cond("axiom",im.module.labeled_axioms)
        
    m = slv.get_model_clauses(ilu.and_clauses(im.module.init_cond,im.module.background_theory()))
    if m == None:
        raise IvyError(None,'Initial condition is inconsistent')
    for sym in all_state_symbols():
        if sym in im.module.params:
            name = varname(sym)
            header.append('    this->{} = {};\n'.format(name,name))
        elif sym not in is_derived:
            assign_symbol_from_model(header,sym,m)
    action = ia.Sequence(*[a for n,a in im.module.initializers])
    action.emit(header)



def emit_constant(self,header,code):
    if (isinstance(self,il.Symbol) and self.is_numeral() and self.sort.name in il.sig.interp
        and il.sig.interp[self.sort.name].startswith('bv[')):
        sname,sparms = parse_int_params(il.sig.interp[self.sort.name])
        code.append('(' + varname(self.name) + ' & ' + str((1 << sparms[0]) -1) + ')')
        return
    code.append(varname(self.name))

il.Symbol.emit = emit_constant
il.Variable.emit = emit_constant

def parse_int_params(name):
    spl = name.split('[')
    name,things = spl[0],spl[1:]
#    print "things:".format(things)
    if not all(t.endswith(']') for t in things):
        raise SyntaxError()
    return name,[int(t[:-1]) for t in things]

def emit_special_op(self,op,header,code):
    if op == 'concat':
        sort_name = il.sig.interp[self.args[1].sort.name]
        sname,sparms = parse_int_params(sort_name)
        if sname == 'bv' and len(sparms) == 1:
            code.append('(')
            self.args[0].emit(header,code)
            code.append(' << {} | '.format(sparms[0]))
            self.args[1].emit(header,code)
            code.append(')')
            return
    if op.startswith('bfe['):
        opname,opparms = parse_int_params(op)
        mask = (1 << (opparms[0]-opparms[1]+1)) - 1
        code.append('(')
        self.args[0].emit(header,code)
        code.append(' >> {} & {})'.format(opparms[1],mask))
        return
    raise iu.IvyError(self,"operator {} cannot be emitted as C++".format(op))

def emit_bv_op(self,header,code):
    sname,sparms = parse_int_params(il.sig.interp[self.sort.name])
    code.append('(')
    code.append('(')
    self.args[0].emit(header,code)
    code.append(' {} '.format(self.func.name))
    self.args[1].emit(header,code)
    code.append(') & {})'.format((1 << sparms[0])-1))

def is_bv_term(self):
    return (il.is_first_order_sort(self.sort)
            and self.sort.name in il.sig.interp
            and il.sig.interp[self.sort.name].startswith('bv['))

def emit_app(self,header,code):
    # handle interpreted ops
    if slv.solver_name(self.func) == None:
        if self.func.name in il.sig.interp:
            op = il.sig.interp[self.func.name]
            emit_special_op(self,op,header,code)
            return
        assert len(self.args) == 2 # handle only binary ops for now
        if is_bv_term(self):
            emit_bv_op(self,header,code)
            return
        code.append('(')
        self.args[0].emit(header,code)
        code.append(' {} '.format(self.func.name))
        self.args[1].emit(header,code)
        code.append(')')
        return 
    # handle uninterpreted ops
    code.append(varname(self.func.name))
    global is_derived
    if self.func in is_derived:
        code.append('(')
        first = True
        for a in self.args:
            if not first:
                code.append(',')
            a.emit(header,code)
            first = False
        code.append(')')
    else: 
        for a in self.args:
            code.append('[')
            a.emit(header,code)
            code.append(']')

lg.Apply.emit = emit_app

temp_ctr = 0

def new_temp(header):
    global temp_ctr
    name = '__tmp' + str(temp_ctr)
    temp_ctr += 1
    indent(header)
    header.append('int ' + name + ';\n')
    return name

def emit_quant(variables,body,header,code,exists=False):
    global indent_level
    if len(variables) == 0:
        body.emit(header,code)
        return
    v0 = variables[0]
    variables = variables[1:]
    res = new_temp(header)
    idx = v0.name
    indent(header)
    header.append(res + ' = ' + str(0 if exists else 1) + ';\n')
    indent(header)
    header.append('for (int ' + idx + ' = 0; ' + idx + ' < ' + str(sort_card(v0.sort)) + '; ' + idx + '++) {\n')
    indent_level += 1
    subcode = []
    emit_quant(variables,body,header,subcode,exists)
    indent(header)
    header.append('if (' + ('!' if not exists else ''))
    header.extend(subcode)
    header.append(') '+ res + ' = ' + str(1 if exists else 0) + ';\n')
    indent_level -= 1
    indent(header)
    header.append('}\n')
    code.append(res)    


lg.ForAll.emit = lambda self,header,code: emit_quant(list(self.variables),self.body,header,code,False)
lg.Exists.emit = lambda self,header,code: emit_quant(list(self.variables),self.body,header,code,True)

def code_line(impl,line):
    indent(impl)
    impl.append(line+';\n')

def code_asgn(impl,lhs,rhs):
    code_line(impl,lhs + ' = ' + rhs)

def code_eval(impl,expr):
    code = []
    expr.emit(impl,code)
    return ''.join(code)

def emit_some(self,header,code):
    vs = [il.Variable('X__'+str(idx),p.sort) for idx,p in enumerate(self.params())]
    subst = dict(zip(self.params(),vs))
    fmla = ilu.substitute_constants_ast(self.fmla(),subst)
    some = new_temp(header)
    code_asgn(header,some,'0')
    if isinstance(self,ivy_ast.SomeMinMax):
        minmax = new_temp(header)
    open_loop(header,vs)
    open_if(header,code_eval(header,fmla))
    if isinstance(self,ivy_ast.SomeMinMax):
        index = new_temp(header)
        idxfmla =  ilu.substitute_constants_ast(self.index(),subst)
        code_asgn(header,index,code_eval(header,idxfmla))
        open_if(header,some)
        sort = self.index().sort
        op = il.Symbol('<',il.RelationSort([sort,sort]))
        idx = il.Symbol(index,sort)
        mm = il.Symbol(minmax,sort)
        pred = op(idx,mm) if isinstance(self,ivy_ast.SomeMin) else op(mm,idx)
        open_if(header,code_eval(header,il.Not(pred)))
        code_line(header,'continue')
        close_scope(header)
        close_scope(header)
        code_asgn(header,minmax,index)
    for p,v in zip(self.params(),vs):
        code_asgn(header,varname(p),varname(v))
    code_line(header,some+'= 1')
    close_scope(header)
    close_loop(header,vs)
    code.append(some)

ivy_ast.Some.emit = emit_some


def emit_unop(self,header,code,op):
    code.append(op)
    self.args[0].emit(header,code)

lg.Not.emit = lambda self,header,code: emit_unop(self,header,code,'!')

def emit_binop(self,header,code,op,ident=None):
    if len(self.args) == 0:
        assert ident != None
        code.append(ident)
        return
    code.append('(')
    self.args[0].emit(header,code)
    for a in self.args[1:]:
        code.append(' ' + op + ' ')
        a.emit(header,code)
    code.append(')')
    
def emit_implies(self,header,code):
    code.append('(')
    code.append('!')
    self.args[0].emit(header,code)
    code.append(' || ')
    self.args[1].emit(header,code)
    code.append(')')
    

lg.Eq.emit = lambda self,header,code: emit_binop(self,header,code,'==')
lg.Iff.emit = lambda self,header,code: emit_binop(self,header,code,'==')
lg.Implies.emit = emit_implies
lg.And.emit = lambda self,header,code: emit_binop(self,header,code,'&&','true')
lg.Or.emit = lambda self,header,code: emit_binop(self,header,code,'||','false')

def emit_assign_simple(self,header):
    code = []
    indent(code)
    self.args[0].emit(header,code)
    code.append(' = ')
    self.args[1].emit(header,code)
    code.append(';\n')    
    header.extend(code)

def emit_assign(self,header):
    global indent_level
    vs = list(lu.free_variables(self.args[0]))
    if len(vs) == 0:
        emit_assign_simple(self,header)
        return
    global temp_ctr
    tmp = '__tmp' + str(temp_ctr)
    temp_ctr += 1
    indent(header)
    header.append('int ' + tmp)
    for v in vs:
        header.append('[' + str(sort_card(v.sort)) + ']')
    header.append(';\n')
    for idx in vs:
        indent(header)
        header.append('for (int ' + idx.name + ' = 0; ' + idx.name + ' < ' + str(sort_card(idx.sort)) + '; ' + idx.name + '++) {\n')
        indent_level += 1
    code = []
    indent(code)
    code.append(tmp + ''.join('['+varname(v.name)+']' for v in vs) + ' = ')
    self.args[1].emit(header,code)
    code.append(';\n')    
    header.extend(code)
    for idx in vs:
        indent_level -= 1
        indent(header)
        header.append('}\n')
    for idx in vs:
        indent(header)
        header.append('for (int ' + idx.name + ' = 0; ' + idx.name + ' < ' + str(sort_card(idx.sort)) + '; ' + idx.name + '++) {\n')
        indent_level += 1
    code = []
    indent(code)
    self.args[0].emit(header,code)
    code.append(' = ' + tmp + ''.join('['+varname(v.name)+']' for v in vs) + ';\n')
    header.extend(code)
    for idx in vs:
        indent_level -= 1
        indent(header)
        header.append('}\n')
    
ia.AssignAction.emit = emit_assign

def emit_havoc(self,header):
    print self
    print self.lineno
    assert False

ia.HavocAction.emit = emit_havoc

def emit_sequence(self,header):
    global indent_level
    indent(header)
    header.append('{\n')
    indent_level += 1
    for a in self.args:
        a.emit(header)
    indent_level -= 1 
    indent(header)
    header.append('}\n')

ia.Sequence.emit = emit_sequence

def emit_assert(self,header):
    code = []
    indent(code)
    code.append('ivy_assert(')
    il.close_formula(self.args[0]).emit(header,code)
    code.append(', "{}");\n'.format(iu.lineno_str(self)))    
    header.extend(code)

ia.AssertAction.emit = emit_assert

def emit_assume(self,header):
    code = []
    indent(code)
    code.append('ivy_assume(')
    il.close_formula(self.args[0]).emit(header,code)
    code.append(', "{}");\n'.format(iu.lineno_str(self)))    
    header.extend(code)

ia.AssumeAction.emit = emit_assume


def emit_call(self,header):
    indent(header)
    header.append('___ivy_stack.push_back(' + str(self.unique_id) + ');\n')
    code = []
    indent(code)
    if len(self.args) == 2:
        self.args[1].emit(header,code)
        code.append(' = ')
    code.append(varname(str(self.args[0].rep)) + '(')
    first = True
    for p in self.args[0].args:
        if not first:
            code.append(', ')
        p.emit(header,code)
        first = False
    code.append(');\n')    
    header.extend(code)
    indent(header)
    header.append('___ivy_stack.pop_back();\n')

ia.CallAction.emit = emit_call

def local_start(header,params,nondet_id=None):
    global indent_level
    indent(header)
    header.append('{\n')
    indent_level += 1
    for p in params:
        indent(header)
        header.append('int ' + varname(p.name) + ';\n')
        if nondet_id != None:
            mk_nondet(header,p.name,sort_card(p.sort),p.name,nondet_id)

def local_end(header):
    global indent_level
    indent_level -= 1
    indent(header)
    header.append('}\n')


def emit_local(self,header):
    local_start(header,self.args[0:-1],self.unique_id)
    self.args[-1].emit(header)
    local_end(header)

ia.LocalAction.emit = emit_local

def emit_if(self,header):
    global indent_level
    code = []
    if isinstance(self.args[0],ivy_ast.Some):
        local_start(header,self.args[0].params())
    indent(code)
    code.append('if(');
    self.args[0].emit(header,code)
    header.extend(code)
    header.append('){\n')
    indent_level += 1
    self.args[1].emit(header)
    indent_level -= 1
    indent(header)
    header.append('}\n')
    if len(self.args) == 3:
        indent(header)
        header.append('else {\n')
        indent_level += 1
        self.args[2].emit(header)
        indent_level -= 1
        indent(header)
        header.append('}\n')
    if isinstance(self.args[0],ivy_ast.Some):
        local_end(header)


ia.IfAction.emit = emit_if

def emit_choice(self,header):
    global indent_level
    if len(self.args) == 1:
        self.args[0].emit(header)
        return
    tmp = new_temp(header)
    mk_nondet(header,tmp,len(self.args),"___branch",self.unique_id)
    for idx,arg in enumerate(self.args):
        indent(header)
        if idx != 0:
            header.append('else ')
        if idx != len(self.args)-1:
            header.append('if(' + tmp + ' == ' + str(idx) + ')');
        header.append('{\n')
        indent_level += 1
        arg.emit(header)
        indent_level -= 1
        indent(header)
        header.append('}\n')

ia.ChoiceAction.emit = emit_choice

def native_reference(atom):
    if isinstance(atom,ivy_ast.Atom) and atom.rep in im.module.actions:
        res = thunk_name(atom.rep) + '(this'
        res += ''.join(', ' + varname(arg.rep) for arg in atom.args) + ')'
        return res
    res = varname(atom.rep)
    for arg in atom.args:
        n = arg.name if hasattr(arg,'name') else arg.rep
        res += '[' + varname(n) + ']'
    return res

def emit_native_action(self,header):
    fields = self.args[0].code.split('`')
    fields = [(native_reference(self.args[int(s)+1]) if idx % 2 == 1 else s) for idx,s in enumerate(fields)]
    indent_code(header,''.join(fields))

ia.NativeAction.emit = emit_native_action

def emit_repl_imports(header,impl,classname):
    pass

def emit_repl_boilerplate1(header,impl,classname):
    impl.append("""

int ask_ret(int bound) {
    int res;
    while(true) {
        std::cout << "? ";
        std::cin >> res;
        if (res >= 0 && res < bound) 
            return res;
        std::cout << "value out of range" << std::endl;
    }
}

""")

    impl.append("""

    class classname_repl : public classname {

    public:

    virtual void ivy_assert(bool truth,const char *msg){
        if (!truth) {
            std::cerr << msg << ": assertion failed\\n";
            exit(1);
        }
    }
    virtual void ivy_assume(bool truth,const char *msg){
        if (!truth) {
            std::cerr << msg << ": assumption failed\\n";
            exit(1);
        }
    }
    """.replace('classname',classname))

    emit_param_decls(impl,classname+'_repl',im.module.params)
    impl.append(' : '+classname+'('+','.join(map(varname,im.module.params))+'){}\n')
    
    for imp in im.module.imports:
        name = imp.imported()
        if not imp.scope() and name in im.module.actions:
            action = im.module.actions[name]
            emit_method_decl(impl,name,action);
            impl.append('{\n    std::cout << "' + name[5:] + '"')
            if action.formal_params:
                impl.append(' << "("')
                first = True
                for arg in action.formal_params:
                    if not first:
                        impl.append(' << ","')
                    first = False
                    impl.append(' << {}'.format(varname(arg.rep.name)))
                impl.append(' << ")"')
            impl.append(' << std::endl;\n')
            if action.formal_returns:
                impl.append('    return ask_ret(__CARD__{});\n'.format(action.formal_returns[0].sort))
            impl.append('}\n')

    

    impl.append("""
    };
""")

    impl.append("""
// Override methods to implement low-level network service

bool is_white(int c) {
    return (c == ' ' || c == '\\t' || c == '\\n');
}

bool is_ident(int c) {
    return c == '_' || c == '.' || (c >= 'A' &&  c <= 'Z')
        || (c >= 'a' &&  c <= 'z')
        || (c >= '0' &&  c <= '9');
}

void skip_white(const std::string& str, int &pos){
    while (pos < str.size() && is_white(str[pos]))
        pos++;
}

struct syntax_error {
};

struct out_of_bounds {
    int idx;
    out_of_bounds(int _idx) : idx(_idx) {}
};

std::string get_ident(const std::string& str, int &pos) {
    std::string res = "";
    while (pos < str.size() && is_ident(str[pos])) {
        res.push_back(str[pos]);
        pos++;
    }
    if (res.size() == 0)
        throw syntax_error();
    return res;
}


void parse_command(const std::string &cmd, std::string &action, std::vector<std::string> &args) {
    int pos = 0;
    skip_white(cmd,pos);
    action = get_ident(cmd,pos);
    skip_white(cmd,pos);
    if (pos < cmd.size() && cmd[pos] == '(') {
        pos++;
        skip_white(cmd,pos);
        args.push_back(get_ident(cmd,pos));
        while(true) {
            skip_white(cmd,pos);
            if (!(pos < cmd.size() && cmd[pos] == ','))
                break;
            pos++;
            args.push_back(get_ident(cmd,pos));
        }
        if (!(pos < cmd.size() && cmd[pos] == ')'))
            throw syntax_error();
        pos++;
    }
    skip_white(cmd,pos);
    if (pos != cmd.size())
        throw syntax_error();
}

struct bad_arity {
    std::string action;
    int num;
    bad_arity(std::string &_action, unsigned _num) : action(_action), num(_num) {}
};

void check_arity(std::vector<std::string> &args, unsigned num, std::string &action) {
    if (args.size() != num)
        throw bad_arity(action,num);
}

int int_arg(std::vector<std::string> &args, unsigned idx, int bound) {
    int res = atoi(args[idx].c_str());
    if (bound && (res < 0 || res >= bound))
        throw out_of_bounds(idx);
    return res;
}


class stdin_reader: public reader {
    std::string buf;

    virtual int fdes(){
        return 0;
    }
    virtual void read() {
        char tmp[257];
        int chars = ::read(0,tmp,256);
        tmp[chars] = 0;
        buf += std::string(tmp);
        size_t pos;
        while ((pos = buf.find('\\n')) != std::string::npos) {
            std::string line = buf.substr(0,pos+1);
            buf.erase(0,pos+1);
            process(line);
        }
    }
    virtual void process(const std::string &line) {
        std::cout << line;
    }
};

class cmd_reader: public stdin_reader {

public:
    classname_repl &ivy;    

    cmd_reader(classname_repl &_ivy) : ivy(_ivy) {
        std::cout << "> "; std::cout.flush();
    }

    virtual void process(const std::string &cmd) {
        std::string action;
        std::vector<std::string> args;
        try {
            parse_command(cmd,action,args);
""".replace('classname',classname))


def emit_repl_boilerplate2(header,impl,classname):
    impl.append("""
            {
                std::cout << "undefined action: " << action << std::endl;
            }
        }
        catch (syntax_error&) {
            std::cout << "syntax error" << std::endl;
        }
        catch (out_of_bounds &err) {
            std::cout << "argument " << err.idx + 1 << " out of bounds" << std::endl;
        }
        catch (bad_arity &err) {
            std::cout << "action " << err.action << " takes " << err.num  << " input parameters" << std::endl;
        }
        std::cout << "> "; std::cout.flush();
    }
};


std::vector<reader *> readers;

void install_reader(reader *r){
    readers.push_back(r);
}

std::vector<timer *> timers;

void install_timer(timer *r){
    timers.push_back(r);
}


int main(int argc, char **argv){
""".replace('classname',classname))

def emit_repl_boilerplate3(header,impl,classname):
    impl.append("""
    install_reader(new cmd_reader(ivy));

    while(true) {

        fd_set rdfds;
        FD_ZERO(&rdfds);
        int maxfds = 0;

        for (unsigned i = 0; i < readers.size(); i++) {
            reader *r = readers[i];
            int fds = r->fdes();
            FD_SET(fds,&rdfds);
            if (fds > maxfds)
                maxfds = fds;
        }

        struct timeval timeout;
        timeout.tv_sec = 1;
        timeout.tv_usec = 0;

        int foo = select(maxfds+1,&rdfds,0,0,&timeout);

        if (foo < 0)
            {perror("select failed"); exit(1);}
        
        if (foo == 0){
            // std::cout << "TIMEOUT\\n";            
           for (unsigned i = 0; i < timers.size(); i++)
               timers[i]->timeout();
        }
        else {
            for (unsigned i = 0; i < readers.size(); i++) {
                reader *r = readers[i];
                if (FD_ISSET(r->fdes(),&rdfds))
                    r->read();
            }
        }            
    }
}
""".replace('classname',classname))


def emit_boilerplate1(header,impl):
    header.append("""
#include <string>
#include <vector>
#include <sstream>
#include <cstdlib>
#include "z3++.h"
#include "hash.h"

using namespace hash_space;

class gen : public ivy_gen {

protected:
    z3::context ctx;
    z3::solver slvr;
    z3::model model;

    gen(): slvr(ctx), model(ctx,(Z3_model)0) {}

    hash_map<std::string, z3::sort> enum_sorts;
    hash_map<Z3_sort, z3::func_decl_vector> enum_values;
    hash_map<std::string, z3::func_decl> decls_by_name;
    hash_map<Z3_symbol,int> enum_to_int;
    std::vector<Z3_symbol> sort_names;
    std::vector<Z3_sort> sorts;
    std::vector<Z3_symbol> decl_names;
    std::vector<Z3_func_decl> decls;
    std::vector<z3::expr> alits;


public:
    z3::expr mk_apply_expr(const char *decl_name, unsigned num_args, const int *args){
        z3::func_decl decl = decls_by_name.find(decl_name)->second;
        std::vector<z3::expr> expr_args;
        unsigned arity = decl.arity();
        assert(arity == num_args);
        for(unsigned i = 0; i < arity; i ++) {
            z3::sort sort = decl.domain(i);
            expr_args.push_back(int_to_z3(sort,args[i]));
        }
        return decl(arity,&expr_args[0]);
    }
    int eval_apply(const char *decl_name, unsigned num_args, const int *args) {
        z3::expr apply_expr = mk_apply_expr(decl_name,num_args,args);
        //        std::cout << "apply_expr: " << apply_expr << std::endl;
        try {
            z3::expr foo = model.eval(apply_expr,true);
            if (foo.is_bv()) {
                assert(foo.is_numeral());
                int v;
                if (Z3_get_numeral_int(ctx,foo,&v) != Z3_TRUE)
                    assert(false && "bit vector value too large for machine int");
                return v;
            }
            assert(foo.is_app());
            if (foo.is_bool())
                return (foo.decl().decl_kind() == Z3_OP_TRUE) ? 1 : 0;
            return enum_to_int[foo.decl().name()];
        }
        catch (const z3::exception &e) {
            std::cout << e << std::endl;
            throw e;
        }
    }

    int eval_apply(const char *decl_name) {
        return eval_apply(decl_name,0,(int *)0);
    }

    int eval_apply(const char *decl_name, int arg0) {
        return eval_apply(decl_name,1,&arg0);
    }
    
    int eval_apply(const char *decl_name, int arg0, int arg1) {
        int args[2] = {arg0,arg1};
        return eval_apply(decl_name,2,args);
    }

    int eval_apply(const char *decl_name, int arg0, int arg1, int arg2) {
        int args[3] = {arg0,arg1,arg2};
        return eval_apply(decl_name,3,args);
    }

    z3::expr int_to_z3(const z3::sort &range, int value) {
        if (range.is_bool())
            return ctx.bool_val(value);
        if (range.is_bv())
            return ctx.bv_val(value,range.bv_size());
        return enum_values.find(range)->second[value]();
    }

    unsigned sort_card(const z3::sort &range) {
        if (range.is_bool())
            return 2;
        if (range.is_bv())
            return 1 << range.bv_size();
        return enum_values.find(range)->second.size();
    }

    int set(const char *decl_name, unsigned num_args, const int *args, int value) {
        z3::func_decl decl = decls_by_name.find(decl_name)->second;
        std::vector<z3::expr> expr_args;
        unsigned arity = decl.arity();
        assert(arity == num_args);
        for(unsigned i = 0; i < arity; i ++) {
            z3::sort sort = decl.domain(i);
            expr_args.push_back(int_to_z3(sort,args[i]));
        }
        z3::expr apply_expr = decl(arity,&expr_args[0]);
        z3::sort range = decl.range();
        z3::expr val_expr = int_to_z3(range,value);
        z3::expr pred = apply_expr == val_expr;
        //        std::cout << "pred: " << pred << std::endl;
        slvr.add(pred);
    }

    int set(const char *decl_name, int value) {
        return set(decl_name,0,(int *)0,value);
    }

    int set(const char *decl_name, int arg0, int value) {
        return set(decl_name,1,&arg0,value);
    }
    
    int set(const char *decl_name, int arg0, int arg1, int value) {
        int args[2] = {arg0,arg1};
        return set(decl_name,2,args,value);
    }

    int set(const char *decl_name, int arg0, int arg1, int arg2, int value) {
        int args[3] = {arg0,arg1,arg2};
        return set(decl_name,3,args,value);
    }

    void randomize(const char *decl_name, unsigned num_args, const int *args) {
        z3::func_decl decl = decls_by_name.find(decl_name)->second;
        z3::expr apply_expr = mk_apply_expr(decl_name,num_args,args);
        z3::sort range = decl.range();
        unsigned card = sort_card(range);
        int value = rand() % card;
        z3::expr val_expr = int_to_z3(range,value);
        z3::expr pred = apply_expr == val_expr;
        // std::cout << "pred: " << pred << std::endl;
        std::ostringstream ss;
        ss << "alit:" << alits.size();
        z3::expr alit = ctx.bool_const(ss.str().c_str());
        alits.push_back(alit);
        slvr.add(!alit || pred);
    }

    void randomize(const char *decl_name) {
        randomize(decl_name,0,(int *)0);
    }

    void randomize(const char *decl_name, int arg0) {
        randomize(decl_name,1,&arg0);
    }
    
    void randomize(const char *decl_name, int arg0, int arg1) {
        int args[2] = {arg0,arg1};
        randomize(decl_name,2,args);
    }

    void randomize(const char *decl_name, int arg0, int arg1, int arg2) {
        int args[3] = {arg0,arg1,arg2};
        randomize(decl_name,3,args);
    }

    void push(){
        slvr.push();
    }

    void pop(){
        slvr.pop();
    }

    void mk_enum(const char *sort_name, unsigned num_values, char const * const * value_names) {
        z3::func_decl_vector cs(ctx), ts(ctx);
        z3::sort sort = ctx.enumeration_sort(sort_name, num_values, value_names, cs, ts);
        // can't use operator[] here because the value classes don't have nullary constructors
        enum_sorts.insert(std::pair<std::string, z3::sort>(sort_name,sort));
        enum_values.insert(std::pair<Z3_sort, z3::func_decl_vector>(sort,cs));
        sort_names.push_back(Z3_mk_string_symbol(ctx,sort_name));
        sorts.push_back(sort);
        for(unsigned i = 0; i < num_values; i++){
            Z3_symbol sym = Z3_mk_string_symbol(ctx,value_names[i]);
            decl_names.push_back(sym);
            decls.push_back(cs[i]);
            enum_to_int[sym] = i;
        }
    }

    void mk_bv(const char *sort_name, unsigned width) {
        z3::sort sort = ctx.bv_sort(width);
        // can't use operator[] here because the value classes don't have nullary constructors
        enum_sorts.insert(std::pair<std::string, z3::sort>(sort_name,sort));
    }

    void mk_decl(const char *decl_name, unsigned arity, const char **domain_names, const char *range_name) {
        std::vector<z3::sort> domain;
        for (unsigned i = 0; i < arity; i++)
            domain.push_back(enum_sorts.find(domain_names[i])->second);
        std::string bool_name("Bool");
        z3::sort range = (range_name == bool_name) ? ctx.bool_sort() : enum_sorts.find(range_name)->second;   
        z3::func_decl decl = ctx.function(decl_name,arity,&domain[0],range);
        decl_names.push_back(Z3_mk_string_symbol(ctx,decl_name));
        decls.push_back(decl);
        decls_by_name.insert(std::pair<std::string, z3::func_decl>(decl_name,decl));
    }

    void mk_const(const char *const_name, const char *sort_name) {
        mk_decl(const_name,0,0,sort_name);
    }

    void add(const std::string &z3inp) {
        z3::expr fmla(ctx,Z3_parse_smtlib2_string(ctx, z3inp.c_str(), sort_names.size(), &sort_names[0], &sorts[0], decl_names.size(), &decl_names[0], &decls[0]));
        ctx.check_error();

        slvr.add(fmla);
    }

    bool solve() {
        // std::cout << alits.size();
        while(true){
            z3::check_result res = slvr.check(alits.size(),&alits[0]);
            if (res != z3::unsat)
                break;
            z3::expr_vector core = slvr.unsat_core();
            if (core.size() == 0)
                return false;
            unsigned idx = rand() % core.size();
            z3::expr to_delete = core[idx];
            for (unsigned i = 0; i < alits.size(); i++)
                if (z3::eq(alits[i],to_delete)) {
                    alits[i] = alits.back();
                    alits.pop_back();
                    break;
                }
        }
        model = slvr.get_model();
        alits.clear();
        //        std::cout << model;
        return true;
    }

    int choose(int rng, const char *name){
        if (decls_by_name.find(name) == decls_by_name.end())
            return 0;
        return eval_apply(name);
    }
};
""")


target = iu.EnumeratedParameter("target",["impl","gen","repl"],"gen")

def main():
    ia.set_determinize(True)
    slv.set_use_native_enums(True)
    iso.set_interpret_all_sorts(True)
    ivy.read_params()
    iu.set_parameters({'coi':'false',"create_imports":'true',"enforce_axioms":'true'})
    if target.get() == "gen":
        iu.set_parameters({'filter_symbols':'false'})
        
    with im.Module():
        ivy.ivy_init()

        classname = im.module.name
        with iu.ErrorPrinter():
            header,impl = module_to_cpp_class(classname)
#        print header
#        print impl
        f = open(classname+'.h','w')
        f.write(header)
        f.close()
        f = open(classname+'.cpp','w')
        f.write(impl)
        f.close()


if __name__ == "__main__":
    main()
        
