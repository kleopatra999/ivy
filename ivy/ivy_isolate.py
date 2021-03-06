#
# Copyright (c) Microsoft Corporation. All Rights Reserved.
#

import ivy_logic
import ivy_dafny_compiler as dc
import ivy_solver as slv
import ivy_logic_utils as lu
import string
import ivy_ast
import ivy_utils as iu
import ivy_actions as ia
import ivy_alpha
import ivy_module as im
import ivy_theory as ith
import ivy_concept_space as ics
from ivy_ast import ASTContext
from collections import defaultdict
import ivy_printer

show_compiled = iu.BooleanParameter("show_compiled",False)
cone_of_influence = iu.BooleanParameter("coi",True)
filter_symbols = iu.BooleanParameter("filter_symbols",True)
create_imports = iu.BooleanParameter("create_imports",False)
enforce_axioms = iu.BooleanParameter("enforce_axioms",False)

def lookup_action(ast,mod,name):
    if name not in mod.actions:
        raise iu.IvyError(ast,"action {} undefined".format(name))
    return mod.actions[name]

def add_mixins(mod,actname,action2,assert_to_assume=lambda m:False,use_mixin=lambda:True,mod_mixin=lambda m:m):
    # TODO: mixins need to be in a fixed order
    assert hasattr(action2,'lineno'), action2
    assert hasattr(action2,'formal_params'), action2
    res = action2
    for mixin in mod.mixins[actname]:
        mixin_name = mixin.args[0].relname
        action1 = lookup_action(mixin,mod,mixin_name)
        assert hasattr(action1,'lineno')
        assert hasattr(action1,'formal_params'), action1
        if use_mixin(mixin_name):
            if assert_to_assume(mixin):
                action1 = action1.assert_to_assume()
                assert hasattr(action1,'lineno')
                assert hasattr(action1,'formal_params'), action1
            action1 = mod_mixin(action1)
            assert hasattr(action1,'lineno')
            assert hasattr(action1,'formal_params'), action1
            res = ia.apply_mixin(mixin,action1,res)
    return res

def summarize_action(action):
    res = ia.Sequence()
    res.lineno = action.lineno
    res.formal_params = action.formal_params
    res.formal_returns = action.formal_returns
    return res

# Delegation of assertions

#    For purposes of compositional proofs, he precondition of an
#    action can be treated as a requirement on the called or as
#    a guarantee of the action when called. In the former case, we say
#    the action *delegates* its precondition to the caller. 

#    Normally, only preconditions equivalent to true can be guaranteed
#    by the action. However, this is not the case in the presense of
#    "before" mixins, since the precondition of the action may be
#    implied by the predondition of the mixin.

#    The default convention is that action *do not* delegate to their
#    callers, but mixins *do*. This gives a way to separated what is
#    guaranteed by the caller from what is guaranteed by the callee.

#    This also means that "opaque" actions can be summarized (see
#    below) since their preconditions must be true. 


# Isolation of components. 

# In each isolate, each component of the hierarchy has one of three
# possible roles:

# 1) Verified. Every assertion delegated to this component is checked.

# 2) Present. Assertions delegated to this component are not checked,
# but the component's actions are not summarized.

# 3) Opaque. The state of this component is abstracted. Its actions are
# summarized.

# Rules for isolation.

# 1) Calls from non-opaque to opaque components.

#    a) are allowed only if the called action does not delegate its
#    assertions to the caller. this is because it is not possible to
#    verify the precondition of the action when its state components
#    are abstracted.

#    b) are allowed only if the called action does not transitively
#    call any non-opaque action. this is because we cannot model the
#    effect of such a call.

#    c) are summarized by null actions

# Conditions (a) and (b) are needed to assume that (c) is sound

# 2) Globally exported actions of opaque components.

#     These are summarized by a single globally exported action that
#     non-deterministically calls all non-opaque actions that are
#     transitively called by a globally exported opaque
#     action. Assertions delegated to this summary action are not
#     checked.

# Rules for the collection of isolates

#     Each assertion must be checked in all possible calling contexts,
#     including external.

#     To guarantee this, we require the following:

#     1) Each non-delegating action must have the verified role in
#     some isolate.

#     2) Each *call* to a delegating action must have the verified role
#     in some isolate.

#     This means that a delegating action that is exported but not
#     internally called will not have its assertions checked. 

# Rules for global export of actions

#     The external version of an action is exported from the isolate if:

#     1) The action is originally globally exported and it is not opaque

#     2) The action is not opaque and is called from any opaque action




interpret_all_sorts = False

def set_interpret_all_sorts(t):
    global interpret_all_sorts
    interpret_all_sorts = t

#def startswith_some(s,prefixes):
#    return any(s.startswith(name+iu.ivy_compose_character) for name in prefixes)

def startswith_some_rec(s,prefixes,mod):
    if s in mod.privates:
        return False
    parts = s.rsplit(iu.ivy_compose_character,1)
    return len(parts)==2 and startswith_eq_some_rec(parts[0],prefixes,mod)

#def startswith_eq_some(s,prefixes):
#    return any(s.startswith(name+iu.ivy_compose_character) or s == name for name in prefixes)

def startswith_eq_some_rec(s,prefixes,mod):
    if s in prefixes:
        return True
    return startswith_some_rec(s,prefixes,mod)

def startswith_some(s,prefixes,mod):
    s = implementation_map.get(s,s)
    return startswith_some_rec(s,prefixes,mod)

def startswith_eq_some(s,prefixes,mod):
    s = implementation_map.get(s,s)
    return startswith_eq_some_rec(s,prefixes,mod)

def strip_map_lookup(name,strip_map,with_dot=False):
    name = canon_act(name)
    for prefix in strip_map:
        if (name+iu.ivy_compose_character).startswith(prefix+iu.ivy_compose_character):
            return strip_map[prefix]
    return []

def presentable(name):
    return str(name).split(':')[-1]

def get_strip_params(name,args,strip_map,strip_binding,ast):
    strip_params = strip_map_lookup(name,strip_map)
    if not(len(args) >= len(strip_params)):
        raise iu.IvyError(ast,"cannot strip isolate parameters from {}".format(presentable(name)))
    for sp,ap in zip(strip_params,args):
        if ap not in strip_binding or strip_binding[ap] != sp:
            raise iu.IvyError(ast,"cannot strip parameter {} from {}".format(presentable(ap),presentable(name)))
    return strip_params

def strip_sort(sort,strip_params):
    dom = list(sort.dom[len(strip_params):])
    if dom or sort.is_relational():
        return ivy_logic.FunctionSort(*(dom+[sort.rng]))
    return sort.rng

def strip_action(ast,strip_map,strip_binding):
    if isinstance(ast,ia.CallAction):
        name = canon_act(ast.args[0].rep)
        args = [strip_action(arg,strip_map,strip_binding) for arg in ast.args[0].args]
        strip_params = get_strip_params(name,ast.args[0].args,strip_map,strip_binding,ast)
        call = ast.args[0].clone(args[len(strip_params):])
        return ast.clone([call]+[strip_action(arg,strip_map,strip_binding) for arg in ast.args[1:]])
    if isinstance(ast,ia.AssignAction):
        if ast.args[0].rep.name in ivy_logic.sig.symbols:
            lhs_params = strip_map_lookup(ast.args[0].rep.name,strip_map)
            if len(lhs_params) != num_isolate_params:
                raise iu.IvyError(ast,"assignment may be interfering")
    if (ivy_logic.is_constant(ast) or ivy_logic.is_variable(ast)) and ast in strip_binding:
        sname = strip_binding[ast]
        if sname not in ivy_logic.sig.symbols:
            ivy_logic.add_symbol(sname,ast.sort)
            strip_added_symbols.append(ivy_logic.Symbol(sname,ast.sort))
        return ivy_logic.Symbol(sname,ast.sort)
    args = [strip_action(arg,strip_map,strip_binding) for arg in ast.args]
    if ivy_logic.is_app(ast):
        name = ast.rep.name
        strip_params = get_strip_params(name,ast.args,strip_map,strip_binding,ast)
        if strip_params:
            new_sort = strip_sort(ast.rep.sort,strip_params)
            new_args = args[len(strip_params):]
            new_symbol = ivy_logic.Symbol(name,new_sort)
            return new_symbol(*new_args)
    if isinstance(ast,ivy_ast.Atom):
        name = ast.rep
        strip_params = get_strip_params(name,ast.args,strip_map,strip_binding,ast)
        if strip_params:
            new_args = args[len(strip_params):]
            return ast.clone(new_args)
    return ast.clone(args)
                
def get_strip_binding(ast,strip_map,strip_binding):
    [get_strip_binding(arg,strip_map,strip_binding) for arg in ast.args]
    name = ast.rep.name if ivy_logic.is_app(ast) else ast.rep if isinstance(ast,ivy_ast.Atom) else None
    if name:
        strip_params = strip_map_lookup(name,strip_map)
        if not(len(ast.args) >= len(strip_params)):
            raise iu.IvyError(ast,"cannot strip isolate parameters from {}".format(presentable(name)))
        for sp,ap in zip(strip_params,ast.args):
            if ap in strip_binding and strip_binding[ap] != sp:
                raise iu.IvyError(action,"cannot strip parameter {} from {}",presentable(ap),presentable(name))
            strip_binding[ap] = sp
                
def strip_labeled_fmla(lfmla,strip_map):
    fmla = lfmla.formula
    strip_binding = {}
    get_strip_binding(fmla,strip_map,strip_binding)
    fmla = strip_action(fmla,strip_map,strip_binding)
    lbl = lfmla.label
    if lbl:
        lbl = lbl.clone(lbl.args[len(strip_map_lookup(lbl.rep,strip_map,with_dot=False)):])
    return lfmla.clone([lbl,fmla])
    
def strip_labeled_fmlas(lfmlas,strip_map):
    new_lfmlas = [strip_labeled_fmla(f,strip_map) for f in lfmlas]
    del lfmlas[:]
    lfmlas.extend(new_lfmlas)
    
def strip_native(native,strip_map):
    strip_binding = {}
    for a in native.args[2:]:
        get_strip_binding(a,strip_map,strip_binding)
    fmlas = [strip_action(fmla,strip_map,strip_binding) for fmla in native.args[2:]]
    lbl = native.args[0]
    if lbl:
        lbl = lbl.clone(lbl.args[len(strip_map_lookup(lbl.rep,strip_map,with_dot=False)):])
    return native.clone([lbl,native.args[1]] + fmlas)
    
def strip_natives(natives,strip_map):
    new_natives = [strip_native(f,strip_map) for f in natives]
    del natives[:]
    natives.extend(new_natives)

def canon_act(name):
    return name[4:] if name.startswith('ext:') else name

def strip_isolate(mod,isolate,impl_mixins,extra_strip):
    global strip_added_symbols
    global num_isolate_params
    num_isolate_params = len(isolate.params())
    ips = set(p.rep for p in isolate.params())
    for atom in isolate.verified()+isolate.present():
        for p in atom.args:
            if p.rep not in ips:
                raise iu.IvyError(p,'unbound isolate parameter: {}'.format(p))
    strip_added_symbols = []
    strip_map = {}
    for atom in isolate.verified() + isolate.present():
        name = atom.relname
        if atom.args:
            if not all(isinstance(v,ivy_ast.App) and not v.args for v in atom.args):
                raise iu.IvyError(atom,"bad isolate parameter")
            for a in atom.args:
                if a.rep in ivy_logic.sig.symbols:
                    raise iu.IvyError(a,"isolate parameter redefines {}",a.rep)
            strip_map[name] = [a.rep for a in atom.args]
    for ms in impl_mixins.values():
        for m in ms:
            if isinstance(m,ivy_ast.MixinImplementDef):
                strip_params = strip_map_lookup(canon_act(m.mixer()),strip_map)
                strip_map[m.mixee()] = strip_params
    strip_map.update(extra_strip)
#    for imp in mod.imports:
#        strip_map[imp.imported()] = [a.rep for a in isolate.params()]
    # strip the actions
    new_actions = {}
    for name,action in mod.actions.iteritems():
        strip_params = strip_map_lookup(canon_act(name),strip_map,with_dot=False)
        if not(len(action.formal_params) >= len(strip_params)):
            raise iu.IvyError(action,"cannot strip isolate parameters from {}".format(name))
        strip_binding = dict(zip(action.formal_params,strip_params))
        if isinstance(action,ia.NativeAction) and len(strip_params) != num_isolate_params:
            raise IvyError(None,'foreign function {} may be interfering'.format(name))
        new_action = strip_action(action,strip_map,strip_binding)
        new_action.formal_params = action.formal_params[len(strip_params):]
        new_action.formal_returns = action.formal_returns
        new_actions[name] = new_action
    mod.actions.clear()
    mod.actions.update(new_actions)

    # strip the axioms and conjectures
    for x in [mod.labeled_axioms,mod.labeled_props,mod.labeled_conjs,mod.labeled_inits]:
        strip_labeled_fmlas(x,strip_map)

    # strip the native quotes
    strip_natives(mod.natives,strip_map)

    # strip the signature
    new_symbols = {}
    for name,sym in ivy_logic.sig.symbols.iteritems():
        strip_params = strip_map_lookup(name,strip_map)
        if strip_params:
            if not (len(sym.sort.dom) >= len(strip_params)):
                raise iu.IvyError(None,"cannot strip isolate parameters from {}",name)
            new_sort = strip_sort(sym.sort,strip_params)
            sym =  ivy_logic.Symbol(name,new_sort)
        new_symbols[name] = sym
    ivy_logic.sig.symbols.clear()
    ivy_logic.sig.symbols.update(new_symbols)

    del mod.params[:]
    add_map = dict((s.name,s) for s in strip_added_symbols)
    used = set()
    for s in isolate.params():
        if not(isinstance(s,ivy_ast.App) and not s.args):
            raise iu.IvyError(isolate,"bad isolate parameter")
        if s.rep in used:
            raise iu.IvyError(isolate,"repeated isolate parameter: {}".format(s.rep))
        used.add(s.rep)
        if s.rep not in add_map:
            raise iu.IvyError(isolate,"unused isolate parameter {}".format(s.rep))
        mod.params.append(add_map[s.rep])

def get_calls_mods(mod,summarized_actions,actname,calls,mods,mixins):
    if actname in calls or actname not in summarized_actions:
        return
    action = mod.actions[actname]
    acalls = set()
    amods = set()
    amixins = set()
    calls[actname] = acalls
    mods[actname] = amods
    mixins[actname] = amixins
    for sub in action.iter_subactions():
        if isinstance(sub,ia.AssignAction):
            sym = sub.args[0].rep
            if sym.name in mod.sig.symbols:
                amods.add(sym.name)
#                iu.dbg('"{}: mods: {} lineno: {}".format(actname,sym.name,sub.lineno)')
#                iu.dbg('action')
        elif isinstance(sub,ia.CallAction):
            calledname = sub.args[0].rep
            if calledname not in summarized_actions:
                acalls.add(calledname)
            get_calls_mods(mod,summarized_actions,calledname,calls,mods,mixins)
            if calledname in calls:
                acalls.update(calls[calledname])
                acalls.update(mixins[calledname]) # tricky -- mixins of callees count as callees
                amods.update(mods[calledname])
    for mixin in mod.mixins[actname]:
        calledname = mixin.args[0].relname
        if calledname not in summarized_actions:
            amixins.add(calledname)
        get_calls_mods(mod,summarized_actions,calledname,calls,mods,mixins)
        if calledname in calls:
            acalls.update(calls[calledname])
            amixins.update(mixins[calledname]) # mixins of mixins count as mixins
            amods.update(mods[calledname])
        
#    iu.dbg('"{}: calls: {} mods: {}".format(actname,acalls,amods)')

def has_unsummarized_mixins(mod,actname,summarized_actions,kind):
    return any(isinstance(mixin,kind) and mixin.args[0].relname not in summarized_actions
               for mixin in mod.mixins[actname])

def get_callouts_action(mod,new_actions,summarized_actions,callouts,action,acallouts,head,tail):
    if isinstance(action,ia.Sequence):
        for idx,sub in enumerate(action.args):
            get_callouts_action(mod,new_actions,summarized_actions,callouts,sub,acallouts,
                                head and idx==0, tail and idx == len(action.args)-1)
    elif isinstance(action,ia.CallAction):
        calledname = action.args[0].rep
        if calledname in summarized_actions:
            if has_unsummarized_mixins(mod,calledname,summarized_actions,ivy_ast.MixinBeforeDef):
                head = False
            if has_unsummarized_mixins(mod,calledname,summarized_actions,ivy_ast.MixinAfterDef):
                tail = False
            acallouts[(3 if tail else 1) if head else (2 if tail else 0)].add(calledname)
        else:
            get_callouts(mod,new_actions,summarized_actions,calledname,callouts)
            # TODO update acallouts correctly
            for a,b in zip(acallouts,callouts[calledname]):
                a.update(b)
    else:
        for sub in action.args:
            if isinstance(sub,ia.Action):
                get_callouts_action(mod,new_actions,summarized_actions,callouts,sub,acallouts,head,tail)
        

def get_callouts(mod,new_actions,summarized_actions,actname,callouts):
    if actname in callouts or actname in summarized_actions:
        return
    acallouts = (set(),set(),set(),set())
    callouts[actname] = acallouts
    action = new_actions[actname]
    get_callouts_action(mod,new_actions,summarized_actions,callouts,action,acallouts,True,True)
    

def check_interference(mod,new_actions,summarized_actions):
    calls = dict()
    mods = dict()
    mixins = dict()
    for actname in summarized_actions:
        get_calls_mods(mod,summarized_actions,actname,calls,mods,mixins)
    callouts = dict()  # these are triples (midcalls,headcalls,tailcalls,bothcalls)
    for actname in new_actions:
        get_callouts(mod,new_actions,summarized_actions,actname,callouts)
#    iu.dbg('callouts')
    for actname,action in new_actions.iteritems():
        if actname not in summarized_actions:
            for called in action.iter_calls():
                if called in summarized_actions:
                    cmods = mods[called]
                    if cmods:
                        things = ','.join(sorted(cmods))
                        raise iu.IvyError(action,"Call out to {} may have visible effect on {}"
                                          .format(called,things))
            if actname in callouts:
                for midcall in sorted(callouts[actname][0]):
                    if midcall in calls:
                        callbacks = calls[midcall]
                        if callbacks:
                            raise iu.IvyError(action,"Call to {} may cause interfering callback to {}"
                                              .format(midcall,','.join(callbacks)))
                
                


def ancestors(s):
    while iu.ivy_compose_character in s:
        yield s
        s,_ = s.rsplit(iu.ivy_compose_character,1)
    yield s

def get_prop_dependencies(mod):
    """ get a list of pairs (p,ds) where p is a property ds is a list
    of objects its proof depends on """
    depmap = defaultdict(list)
    for iso in mod.isolates.values():
        for v in iso.verified():
            depmap[v.rep].extend(w.rep for w in iso.verified()+iso.present())
    objs = set()
    for ax in mod.labeled_axioms:
        if ax.label:
            for n in ancestors(ax.label.rep):
                objs.add(n)
    for itps in mod.interps.values():
        for itp in itps:
            if itp.label:
                for n in ancestors(itp.label.rep):
                    objs.add(n)        
    res = []
    for prop in mod.labeled_props:
        if prop.label:
            ds = []
            for n in ancestors(prop.label.rep):
                ds.extend(d for d in depmap[n] if d in objs)
            res.append((prop,ds))
    return res

def isolate_component(mod,isolate_name,extra_with=[],extra_strip=None):
    if isolate_name not in mod.isolates:
        raise iu.IvyError(None,"undefined isolate: {}".format(isolate_name))
    isolate = mod.isolates[isolate_name]
    verified = set(a.relname for a in (isolate.verified()+tuple(extra_with)))
    present = set(a.relname for a in isolate.present())
    present.update(verified)
    if not interpret_all_sorts:
        for type_name in list(ivy_logic.sig.interp):
            if not (type_name in present or any(startswith_eq_some(itp.label.rep,present,mod) for itp in mod.interps[type_name] if itp.label)):
                del ivy_logic.sig.interp[type_name]
    delegates = set(s.delegated() for s in mod.delegates if not s.delegee())
    delegated_to = dict((s.delegated(),s.delegee()) for s in mod.delegates if s.delegee())
    derived = set(df.args[0].func.name for df in mod.concepts)
    for name in present:
        if (name not in mod.hierarchy
            and name not in ivy_logic.sig.sorts
            and name not in derived
            and name not in ivy_logic.sig.interp
            and name not in mod.actions
            and name not in ivy_logic.sig.symbols):
            raise iu.IvyError(None,"{} is not an object, action, sort, definition, or interpreted function".format(name))
    
    impl_mixins = defaultdict(list)
    # delegate all the stub actions to their implementations
    global implementation_map
    implementation_map = {}
    for actname,ms in mod.mixins.iteritems():
        implements = [m for m in ms if isinstance(m,ivy_ast.MixinImplementDef)]
        impl_mixins[actname].extend(implements)
        before_after = [m for m in ms if not isinstance(m,ivy_ast.MixinImplementDef)]
        del ms[:]
        ms.extend(before_after)
        for m in implements:
            for foo in (m.mixee(),m.mixer()):
                if foo not in mod.actions:
                    raise IvyError(m,'action {} not defined'.format(foo))
            action = mod.actions[m.mixee()]
            if not (isinstance(action,ia.Sequence) and len(action.args) == 0):
                raise IvyError(m,'multiple implementations of action {}'.format(m.mixee()))
            action = ia.apply_mixin(m,mod.actions[m.mixer()],action)
            mod.actions[m.mixee()] = action
            implementation_map[m.mixee()] = m.mixer()

    new_actions = {}
    use_mixin = lambda name: startswith_some(name,present,mod)
    mod_mixin = lambda m: m if startswith_some(name,verified,mod) else m.prefix_calls('ext:')
    all_mixins = lambda m: True
    no_mixins = lambda m: False
    after_mixins = lambda m: isinstance(m,ivy_ast.MixinAfterDef)
    before_mixins = lambda m: isinstance(m,ivy_ast.MixinBeforeDef)
    delegated_to_verified = lambda n: n in delegated_to and startswith_eq_some(delegated_to[n],verified,mod)
    ext_assumes = lambda m: before_mixins(m) and not delegated_to_verified(m.mixer())
    int_assumes = lambda m: after_mixins(m) and not delegated_to_verified(m.mixer())
    ext_assumes_no_ver = lambda m: not delegated_to_verified(m.mixer())
    summarized_actions = set()
    for actname,action in mod.actions.iteritems():
        ver = startswith_eq_some(actname,verified,mod)
        pre = startswith_eq_some(actname,present,mod)
        if pre: 
            if not ver:
                assert hasattr(action,'lineno')
                assert hasattr(action,'formal_params'), action
                ext_action = action.assert_to_assume().prefix_calls('ext:')
                assert hasattr(ext_action,'lineno')
                assert hasattr(ext_action,'formal_params'), ext_action
                if actname in delegates:
                    int_action = action.prefix_calls('ext:')
                    assert hasattr(int_action,'lineno')
                    assert hasattr(int_action,'formal_params'), int_action
                else:
                    int_action = ext_action
                    assert hasattr(int_action,'lineno')
                    assert hasattr(int_action,'formal_params'), int_action
            else:
                int_action = ext_action = action
                assert hasattr(int_action,'lineno')
                assert hasattr(int_action,'formal_params'), int_action
            # internal version of the action has mixins checked
            ea = no_mixins if ver else int_assumes
            new_actions[actname] = add_mixins(mod,actname,int_action,ea,use_mixin,lambda m:m)
            # external version of the action assumes mixins are ok, unless they
            # are delegated to a currently verified object
            ea = ext_assumes if ver else ext_assumes_no_ver
            new_action = add_mixins(mod,actname,ext_action,ea,use_mixin,mod_mixin)
            new_actions['ext:'+actname] = new_action
            # TODO: external version is public if action public *or* called from opaque
            # public_actions.add('ext:'+actname)
        else:
            # TODO: here must check that summarized action does not
            # have a call dependency on the isolated module
            summarized_actions.add(actname)
            action = summarize_action(action)
            new_actions[actname] = add_mixins(mod,actname,action,after_mixins,use_mixin,mod_mixin)
            new_actions['ext:'+actname] = add_mixins(mod,actname,action,all_mixins,use_mixin,mod_mixin)

    # figure out what is exported:
    exported = set()
    for e in mod.exports:
        if not e.scope() and startswith_eq_some(e.exported(),present,mod): # global scope
            exported.add('ext:' + e.exported())
    for actname,action in mod.actions.iteritems():
        if not startswith_some(actname,present,mod):
            for c in action.iter_calls():
                if (startswith_some(c,present,mod)
                    or any(startswith_some(m.mixer(),present,mod) for m in mod.mixins[c])) :
                        exported.add('ext:' + c)
#    print "exported: {}".format(exported)


    # We allow objects to reference any symbols in global scope, and
    # we keep axioms declared in global scope. Because of the way
    # thigs are named, this gives a different condition for keeping
    # symbols and axioms (in particular, axioms in global scope have
    # label None). Maybe this needs to be cleaned up.

    keep_sym = lambda name: (iu.ivy_compose_character not in name
                            or startswith_eq_some(name,present))
    
    keep_ax = lambda name: (name is None or startswith_eq_some(name.rep,present,mod))
    check_pr = lambda name: (name is None or startswith_eq_some(name.rep,verified,mod))

    prop_deps = get_prop_dependencies(mod)

    # filter the conjectures

    new_conjs = [c for c in mod.labeled_conjs if keep_ax(c.label)]
    del mod.labeled_conjs[:]
    mod.labeled_conjs.extend(new_conjs)

    # filter the inits

    new_inits = [c for c in mod.labeled_inits if keep_ax(c.label)]
    del mod.labeled_inits[:]
    mod.labeled_inits.extend(new_inits)
    
    # filter the axioms
    dropped_axioms = [a for a in mod.labeled_axioms if not keep_ax(a.label)]
    mod.labeled_axioms = [a for a in mod.labeled_axioms if keep_ax(a.label)]
    mod.labeled_props = [a for a in mod.labeled_props if keep_ax(a.label)]

    # convert the properties not being verified to axioms
    mod.labeled_axioms.extend([a for a in mod.labeled_props if not check_pr(a.label)])
    mod.labeled_props =  [a for a in mod.labeled_props if check_pr(a.label)]

    # filter definitions
    mod.concepts = [c for c in mod.concepts if startswith_eq_some(c.args[0].func.name,present,mod)]


    # filter the signature
    # keep only the symbols referenced in the remaining
    # formulas

    asts = []
    for x in [mod.labeled_axioms,mod.labeled_props,mod.labeled_inits,mod.labeled_conjs]:
        asts += [y.formula for y in x]
    asts += mod.concepts
    asts += [action for action in new_actions.values()]
    sym_names = set(x.name for x in lu.used_symbols_asts(asts))

    if filter_symbols.get() or cone_of_influence.get():
        old_syms = list(mod.sig.symbols)
        for sym in old_syms:
            if sym not in sym_names:
                del mod.sig.symbols[sym]

    # check that any dropped axioms do not refer to the isolate's signature
    # and any properties have dependencies present

    def pname(s):
        return s.label if s.label else ""

    if enforce_axioms.get():
        for a in dropped_axioms:
            for x in lu.used_symbols_ast(a.formula):
                if x.name in sym_names:
                    raise iu.IvyError(a,"relevant axiom {} not enforced".format(pname(a)))
        for actname,action in mod.actions.iteritems():
            if startswith_eq_some(actname,present,mod):
                for c in action.iter_calls():
                    called = mod.actions[c]
                    if not startswith_eq_some(c,present,mod):
                        if not(type(called) == ia.Sequence and not called.args):
                            raise iu.IvyError(None,"No implementation for action {}".format(c))
        for p,ds in prop_deps:
            for d in ds:
                if not startswith_eq_some(d,present,mod):
                    raise iu.IvyError(p,"property {} depends on abstracted object {}"
                                      .format(pname(p),d))

#    for x,y in new_actions.iteritems():
#        print iu.pretty(ia.action_def_to_str(x,y))

    # check for interference

#    iu.dbg('list(summarized_actions)')
    check_interference(mod,new_actions,summarized_actions)


    # After checking, we can put in place the new action definitions

    mod.public_actions.clear()
    mod.public_actions.update(exported)
    mod.actions.clear()
    mod.actions.update(new_actions)

    # TODO: need a better way to filter signature
    # new_syms = set(s for s in mod.sig.symbols if keep_sym(s))
    # for s in list(mod.sig.symbols):
    #     if s not in new_syms:
    #         del mod.sig.symbols[s]



    # strip the isolate parameters

    strip_isolate(mod,isolate,impl_mixins,extra_strip)

    # collect the initial condition

    init_cond = ivy_logic.And(*(lf.formula for lf in mod.labeled_inits))
    mod.init_cond = lu.formula_to_clauses(init_cond)



class SortOrder(object):
    def __init__(self,arcs):
        self.arcs = arcs
    def __call__(self,x,y):
        x = x.args[0].relname
        y = y.args[0].relname
        res =  -1 if y in self.arcs[x] else 1 if x in self.arcs[y] else 0   
        return res

# class SortOrder(object):
#     def __init__(self,arcs):
#         self.arcs = arcs
#     def __call__(self,x,y):
#         x = x.args[0].relname
#         y = y.args[0].relname
#         res =  -1 if y in self.arcs[x] else 1 if x in self.arcs[y] else 0   
#         return res


def get_mixin_order(iso,mod):
    arcs = [(rdf.args[0].relname,rdf.args[1].relname) for rdf in mod.mixord]
    actions = mod.mixins.keys()
    for action in actions:
        mixins = mod.mixins[action]
        implements = [m for m in mixins if isinstance(m,ivy_ast.MixinImplementDef)]
        if len(implements) > 1:
            raise iu.IvyError(implements[1],'Multiple implementations for {}'.format(action))
        mixins = [m for m in mixins if not isinstance(m,ivy_ast.MixinImplementDef)]
        mixers = iu.topological_sort(list(set(m.mixer() for m in mixins)),arcs)
        keymap = dict((x,y) for y,x in enumerate(mixers))
        key = lambda m: keymap[m.mixer()]
        before = sorted([m for m in mixins if isinstance(m,ivy_ast.MixinBeforeDef)],key=key)
        after = sorted([m for m in mixins if isinstance(m,ivy_ast.MixinAfterDef)],key=key)
#        order = SortOrder(arcs)
#        before = sorted([m for m in mixins if isinstance(m,ivy_ast.MixinBeforeDef)],order)
#        after = sorted([m for m in mixins if isinstance(m,ivy_ast.MixinAfterDef)],order)
        before.reverse() # add the before mixins in reverse order
        mixins = implements + before + after
#        print 'mixin order for action {}:'
#        for m in mixins:
#            print m.args[0]
        mod.mixins[action] = mixins
        

ext_action = iu.Parameter("ext",None)

def hide_action_params(action):
    params = action.formal_params + action.formal_returns
    res = ia.LocalAction(*(params + [action]))
    return res

def get_cone(mod,action_name,cone):
    if action_name not in cone:
        cone.add(action_name)
        for a in mod.actions[action_name].iter_calls():
            get_cone(mod,a,cone)

def get_mod_cone(mod):
    cone = set()
    for a in mod.public_actions:
        get_cone(mod,a,cone)
    for n in mod.natives:
        for a in n.args[2:]:
            if isinstance(a,ivy_ast.Atom) and a.rep in mod.actions:
                get_cone(mod,a.rep,cone)
    return cone

def loop_action(action,mod):
    subst = dict((p,ivy_logic.Variable('Y'+p.name,p.sort)) for p in action.formal_params)
    action = lu.substitute_constants_ast(action,subst)
    ia.type_check_action(action,mod) # make sure all is legal
    return action

def fix_initializers(mod,after_inits):
        for m in after_inits:
            name = m.mixer()
            extname = 'ext:'+name
            action = mod.actions[extname] if extname in mod.actions else mod.actions[name]
            if not ia.has_code(action):
                continue
            mod.initializers.append((name,loop_action(action,mod)))
            if name in mod.actions:
                del mod.actions[name]
            if name in mod.public_actions:
                mod.public_actions.remove(name)
            if extname in mod.actions:
                del mod.actions[extname]
            if extname in mod.public_actions:
                mod.public_actions.remove(extname)
        ais = set(m.mixer() for m in after_inits)
        mod.exports = [e for e in mod.exports if e.exported() not in ais]

def create_isolate(iso,mod = None,**kwargs):

        mod = mod or im.module

        # treat initializers as exports
        after_inits = mod.mixins["init"]
        del  mod.mixins["init"]
        mod.exports.extend(ivy_ast.ExportDef(ivy_ast.Atom(a.mixer()),ivy_ast.Atom('')) for a in after_inits)

        # check all mixin declarations

        for name,mixins in mod.mixins.iteritems():
            for mixin in mixins:
                with ASTContext(mixins):
                    action1,action2 = (lookup_action(mixin,mod,a.relname) for a in mixin.args)

        # check all the delagate declarations

        for dl in mod.delegates:
            lookup_action(dl.args[0],mod,dl.delegated())
            if dl.delegee() and dl.delegee() not in mod.hierarchy:
                raise iu.IvyError(dl.args[1],"{} is not a module instance".format(name))

        # check all the export declarations
        for exp in mod.exports:
            expname = exp.args[0].rep
            if expname not in mod.actions:
                raise iu.IvyError(exp,"undefined action: {}".format(expname))

        # create the import actions, if requested

        extra_with = []
        extra_strip = {}
        if create_imports.get():
            newimps = []
            for imp in mod.imports:
                if imp.args[1].rep == '':
                    impname = imp.args[0].rep
                    if impname not in mod.actions:
                        raise iu.IvyError(imp,"undefined action: {}".format(impname))
                    action = mod.actions[impname]
                    if not(type(action) == ia.Sequence and not action.args):
                        raise iu.IvyError(imp,"cannot import implemented action: {}".format(impname))
                    extname = 'imp__' + impname
                    call = ia.CallAction(*([ivy_ast.Atom(extname,action.formal_params)] + action.formal_returns))
                    call.formal_params = action.formal_params
                    call.formal_returns = action.formal_returns
                    call.lineno = action.lineno
                    mod.actions[impname] = call
                    mod.actions[extname] = action
                    newimps.append(ivy_ast.ImportDef(ivy_ast.Atom(extname),imp.args[1]))
                    extra_with.append(ivy_ast.Atom(impname))
#                    extra_with.append(ivy_ast.Atom(extname))
                    if iso and iso in mod.isolates:
                        ps = mod.isolates[iso].params()
                        extra_strip[impname] = [a.rep for a in ps]
                        extra_strip[extname] = [a.rep for a in ps]
                else:
                    newimps.append(imp)
            mod.imports = newimps

        mixers = set()
        for ms in mod.mixins.values():
            for m in ms:
                mixers.add(m.mixer())

        # Determine the mixin order (as a side effect on module.mixins)

        get_mixin_order(iso,mod)

        # Construct an isolate

        if iso:
            isolate_component(mod,iso,extra_with=extra_with,extra_strip=extra_strip)
        else:
            if mod.isolates and cone_of_influence.get():
                raise iu.IvyError(None,'no isolate specified on command line')
            # apply all the mixins in no particular order
            for name,mixins in mod.mixins.iteritems():
                for mixin in mixins:
                    action1,action2 = (lookup_action(mixin,mod,a.relname) for a in mixin.args)
                    mixed = ia.apply_mixin(mixin,action1,action2)
                    mod.actions[mixin.args[1].relname] = mixed
            # find the globally exported actions (all if none specified, for compat)
            if mod.exports:
                mod.public_actions.clear()
                for e in mod.exports:
                    if not e.scope(): # global export
                        mod.public_actions.add(e.exported())
            else:
                for a in mod.actions:
                    mod.public_actions.add(a)

        # Create one big external action if requested


        for name in mod.public_actions:
            mod.actions[name].label = name
        ext = kwargs['ext'] if 'ext' in kwargs else ext_action.get()
        if ext is not None:
            ext_acts = [mod.actions[x] for x in sorted(mod.public_actions)]
            ext_act = ia.EnvAction(*ext_acts)
            mod.public_actions.add(ext);
            mod.actions[ext] = ext_act;

        # Check native interpretations of symbols

        slv.check_compat()

        # Make concept spaces from the conjecture

        for i,cax in enumerate(mod.labeled_conjs):
            fmla = cax.formula
            csname = 'conjecture:'+ str(i)
            variables = list(lu.used_variables_ast(fmla))
            sort = ivy_logic.RelationSort([v.sort for v in variables])
            sym = ivy_logic.Symbol(csname,sort)
            space = ics.NamedSpace(ivy_logic.Literal(0,fmla))
            mod.concept_spaces.append((sym(*variables),space))

        ith.check_theory()

        # get rid of useless actions

        cone = get_mod_cone(mod)        
        if cone_of_influence.get():
            for a in list(mod.actions):
                if a not in cone:
                    del mod.actions[a]
        else:
            for a in list(mod.actions):
                if a not in cone and not a.startswith('ext:') and a not in mixers:
                    ea = 'ext:' + a
                    if ea in mod.actions and ea not in cone:
                        if ia.has_code(mod.actions[a]):
                            iu.warn(mod.actions[a],"action {} is never called".format(a))

        fix_initializers(mod,after_inits)

        # show the compiled code if requested

        if show_compiled.get():
            ivy_printer.print_module(mod)



def has_assertions(mod,callee):
    return any(isinstance(action,ia.AssertAction) for action in mod.actions[callee].iter_subactions())

def find_some_assertion(mod,actname):
    for action in mod.actions[actname].iter_subactions():
        if isinstance(action,ia.AssertAction):
            return action
    return None

def find_some_call(mod,actname,callee):
    for action in mod.actions[actname].iter_subactions():
        if isinstance(action,ia.CallAction) and action.callee() == callee:
            return action
    return None

def check_isolate_completeness(mod = None):
    mod = mod or im.module
    checked = set()
    checked_props = set()
    checked_context = defaultdict(set) # maps action name to set of delegees
    delegates = set(s.delegated() for s in mod.delegates if not s.delegee())
    delegated_to = dict((s.delegated(),s.delegee()) for s in mod.delegates if s.delegee())
    global implementation_map
    implementation_map = {}
    for iso_name,isolate in mod.isolates.iteritems():
        verified = set(a.relname for a in isolate.verified())
        present = set(a.relname for a in isolate.present())
        present.update(verified)
        verified_actions = set(a for a in mod.actions if startswith_eq_some(a,verified,mod))
        present_actions = set(a for a in mod.actions if startswith_eq_some(a,present,mod))
        for a in verified_actions:
            if a not in delegates:
                checked.add(a)
        for a in present_actions:
            checked_context[a].update(verified_actions)
        for prop in mod.labeled_props:
            if prop.label:
                label = prop.label.relname
                if startswith_eq_some(label,verified,mod):
                    checked_props.add(label)
            
    missing = []
    trusted = set()
    for n in mod.natives:
        lbl = n.args[0]
        if lbl:
            trusted.add(lbl.rep)
            
    for actname,action in mod.actions.iteritems():
        if startswith_eq_some(actname,trusted,mod):
            continue
        for callee in action.iter_calls():
            if not (callee in checked or not has_assertions(mod,callee)
                    or callee in delegates and actname in checked_context[callee]):
                missing.append((actname,callee,None))
            for mixin in mod.mixins[callee]:
                mixed = mixin.args[0].relname
                if not has_assertions(mod,mixed):
                    continue
                verifier = actname if isinstance(mixin,ivy_ast.MixinBeforeDef) else callee
                if verifier not in checked_context[mixed]:
                        missing.append((actname,mixin,None))
    for e in mod.exports:
        if e.scope(): # global export
            continue
        callee = e.exported()
        if not (callee in checked or not has_assertions(mod,callee) or actname in delegates):
            missing.append(("external",callee,None))
        for mixin in mod.mixins[callee]:
            mixed = mixin.args[0].relname
            if has_assertions(mod,mixed) and not isinstance(mixin,ivy_ast.MixinBeforeDef):
                missing.append(("external",mixin,None))
        
    if missing:
        for x,y,z in missing:
            mixer = y.mixer() if isinstance(y,ivy_ast.MixinDef) else y
            mixee = y.mixee() if isinstance(y,ivy_ast.MixinDef) else y
            print iu.IvyError(find_some_assertion(mod,mixer),"assertion is not checked")
            if mixee != mixer:
                print iu.IvyError(mod.actions[mixee],"...in action {}".format(mixee))
            print iu.IvyError(find_some_call(mod,x,mixee),"...when called from {}".format(x))
    
    done = set()
    for prop in mod.labeled_props:
        if prop.label:
            label = prop.label.relname
            if label not in checked_props and label not in done:
                print iu.IvyError(prop,"property {} not checked".format(label))
                missing.append((label,None,None))
                done.add(label)
        
    return missing
