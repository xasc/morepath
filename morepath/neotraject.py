import re
from functools import total_ordering
from morepath import generic
from reg import Registry

IDENTIFIER = re.compile(r'^[^\d\W]\w*$')
PATH_VARIABLE = re.compile(r'\{([^}]*)\}')
VARIABLE = '{}'

# XXX need to make this pluggable
KNOWN_CONVERTERS = {
    'str': str,
    'int': int
    }

CONVERTER_WEIGHT = {
    str: 0,
    int: 1
    }

class TrajectError(Exception):
    pass


@total_ordering
class Step(object):
    def __init__(self, s):
        self.s = s
        self.generalized = generalize_variables(s)
        self.parts = tuple(self.generalized.split('{}'))
        self._variables_re = create_variables_re(s)
        parser = NameParser(KNOWN_CONVERTERS)
        self.names, self.converters = parse_variables(s)
        self.validate()
        self._converter_weight = sum(
            [CONVERTER_WEIGHT[c] for c in self.converters])
        if len(set(self.names)) != len(self.names):
            raise TrajectError("Duplicate variable")

    def validate(self):
        self.validate_parts()
        self.validate_variables()

    def validate_parts(self):
        # XXX should also check for valid URL characters
        for part in self.parts:
            if '{' in part or '}' in part:
                raise TrajectError("invalid step: %s" % self.s)

    def validate_variables(self):
        parts = self.parts
        if parts[0] == '':
            parts = parts[1:]
        if parts[-1] == '':
            parts = parts[:-1]
        for part in parts:
            if part == '':
                raise TrajectError(
                    "illegal consecutive variables: %s" % self.s)

    def has_variables(self):
        return bool(self.names)

    def match(self, s):
        result = {}
        matched = self._variables_re.match(s)
        if matched is None:
            return False, result
        for name, converter, value in zip(self.names, self.converters,
                                          matched.groups()):
            try:
                result[name] = converter(value)
            except ValueError:
                return False, {}
        return True, result

    def __eq__(self, other):
        return self.s == other.s

    def __lt__(self, other):
        if self.parts == other.parts:
            # more converter weight is more specific
            return self._converter_weight > other._converter_weight
            # XXX what if converter weight is the same?
        if self._variables_re.match(other.s) is not None:
            return False
        if other._variables_re.match(self.s) is not None:
            return True
        return self.parts > other.parts


class Node(object):
    def __init__(self):
        self._name_nodes = {}
        self._variable_nodes = []
        self.value = None

    def add(self, step):
        if not step.has_variables():
            return self.add_name_node(step)
        return self.add_variable_node(step)

    def add_name_node(self, step):
        node = self._name_nodes.get(step.s)
        if node is not None:
            return node
        node = StepNode(step)
        self._name_nodes[step.s] = node
        return node

    def add_variable_node(self, step):
        # XXX conflict system should prevent double insertions before this
        for i, node in enumerate(self._variable_nodes):
            if node.step.s == step.s:
                return node
            if step > node.step:
                continue
            result = StepNode(step)
            self._variable_nodes.insert(i, result)
            return result
        result = StepNode(step)
        self._variable_nodes.append(result)
        return result

    def get(self, segment):
        node = self._name_nodes.get(segment)
        if node is not None:
            return node, {}
        for node in self._variable_nodes:
            matched, variables = node.match(segment)
            if matched:
                return node, variables
        return None, {}


class StepNode(Node):
    def __init__(self, step):
        super(StepNode, self).__init__()
        self.step = step

    def match(self, segment):
        return self.step.match(segment)


class Traject(Node):
    def __init__(self):
        super(Traject, self).__init__()
        self._inverse = Registry() # XXX caching

    def add_pattern(self, segments, value):
        node = self
        known_variables = set()
        for segment in segments:
            step = Step(segment)
            node = node.add(step)
            variables = set(step.names)
            if known_variables.intersection(variables):
                raise TrajectError("Duplicate variables")
            known_variables.update(variables)
        node.value = value

    def inverse(self, model_class, path, get_variables):
        path = interpolation_path(path)
        self._inverse.register('inverse',
                               [model_class], (path, get_variables))

    def __call__(self, stack):
        stack = stack[:]
        node = self
        variables = {}
        while stack:
            segment = stack.pop()
            new_node, new_variables = node.get(segment)
            if new_node is None:
                stack.append(segment)
                return node.value, stack, variables
            node = new_node
            variables.update(new_variables)
        return node.value, stack, variables

    def path(self, model):
        path, get_variables = self._inverse.component('inverse', [model])
        variables = get_variables(model)
        assert isinstance(variables, dict)
        return path % variables


class NameParser(object):
    def __init__(self, known_converters):
        self.known_converters = known_converters

    def __call__(self, s):
        parts = s.split(':')
        if len(parts) > 2:
            raise TrajectError(
                "illegal variable: %s" % s)
        if len(parts) == 1:
            name = s.strip()
            converter_id = 'str'
        else:
            name, converter_id = parts
            name = name.strip()
            converter_id = converter_id.strip()
        if not is_identifier(name):
            raise TrajectError(
                "illegal variable identifier: %s" % name)
        converter = self.known_converters.get(converter_id)
        if converter is None:
            raise TrajectError("unknown converter: %s" % converter_id)
        return name, converter


def traject_consumer(base, stack, lookup):
    traject = generic.traject(base, lookup=lookup, default=None)
    if traject is None:
        return False, base, stack
    original_stack = stack[:]
    get_model, stack, variables = traject(stack)
    if get_model is None:
        return False, base, original_stack
    model = get_model(**variables)
    if model is None:
        return False, base, original_stack
    return True, model, stack

    while stack:
        step = stack.pop()
        next_pattern, matched = traject.match(pattern, step)
        variables.update(matched)
        if next_pattern is None:
            # put the last step back onto the stack
            stack.append(step)
            break
        consumed.append(step)
        pattern = next_pattern
    model = traject.get_model(base, pattern, variables)
    if model is None:
        # put everything we tried to consume back on stack
        stack.extend(reversed(consumed))
        return False, base, stack
    return True, model, stack


def is_identifier(s):
    return IDENTIFIER.match(s) is not None


def parse_variables(s):
    names = []
    converters = []
    parser = NameParser(KNOWN_CONVERTERS)
    variables = PATH_VARIABLE.findall(s)
    for variable in variables:
        name, converter = parser(variable)
        names.append(name)
        converters.append(converter)
        # XXX error if name is already seen?
    return names, converters


def create_variables_re(s):
    return re.compile('^' + PATH_VARIABLE.sub(r'(.+)', s) + '$')


def generalize_variables(s):
    return PATH_VARIABLE.sub('{}', s)


def interpolation_path(s):
    return PATH_VARIABLE.sub(r'%(\1)s', s)