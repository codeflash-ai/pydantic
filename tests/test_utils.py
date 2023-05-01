import collections.abc
import os
import pickle
import sys
from copy import copy, deepcopy
from typing import Callable, Dict, Generic, List, NewType, Tuple, TypeVar, Union

import pytest
from dirty_equals import IsList
from pydantic_core import PydanticCustomError
from typing_extensions import Annotated, Literal

from pydantic import BaseModel
from pydantic._internal import _repr
from pydantic._internal._typing_extra import all_literal_values, get_origin, is_new_type
from pydantic._internal._utils import (
    BUILTIN_COLLECTIONS,
    ClassAttribute,
    ValueItems,
    all_identical,
    deep_update,
    lenient_issubclass,
    smart_deepcopy,
    to_lower_camel,
    unique_list,
)
from pydantic._internal._validators import import_string
from pydantic.color import Color
from pydantic.fields import Undefined

try:
    import devtools
except ImportError:
    devtools = None


def test_import_module():
    assert import_string('os.path') == os.path


def test_import_module_invalid():
    with pytest.raises(PydanticCustomError, match='Invalid python path: "xx" doesn\'t look like a module path'):
        import_string('xx')


def test_import_no_attr():
    with pytest.raises(PydanticCustomError, match='Module "os" does not define a "foobar" attribute'):
        import_string('os.foobar')


def foobar(a, b, c=4):
    pass


T = TypeVar('T')


class LoggedVar(Generic[T]):
    def get(self) -> T:
        ...


@pytest.mark.parametrize(
    'value,expected',
    [
        (str, 'str'),
        ('foobar', 'str'),
        ('SomeForwardRefString', 'str'),  # included to document current behavior; could be changed
        (List['SomeForwardRef'], "List[ForwardRef('SomeForwardRef')]"),  # noqa: F821
        (Union[str, int], 'Union[str, int]'),
        (list, 'list'),
        (List, 'List'),
        ([1, 2, 3], 'list'),
        (List[Dict[str, int]], 'List[Dict[str, int]]'),
        (Tuple[str, int, float], 'Tuple[str, int, float]'),
        (Tuple[str, ...], 'Tuple[str, ...]'),
        (Union[int, List[str], Tuple[str, int]], 'Union[int, List[str], Tuple[str, int]]'),
        (foobar, 'foobar'),
        (LoggedVar, 'LoggedVar'),
        (LoggedVar(), 'LoggedVar'),
    ],
)
def test_display_as_type(value, expected):
    assert _repr.display_as_type(value) == expected


@pytest.mark.skipif(sys.version_info < (3, 10), reason='requires python 3.10 or higher')
@pytest.mark.parametrize(
    'value_gen,expected',
    [
        (lambda: str, 'str'),
        (lambda: 'SomeForwardRefString', 'str'),  # included to document current behavior; could be changed
        (lambda: List['SomeForwardRef'], "List[ForwardRef('SomeForwardRef')]"),  # noqa: F821
        (lambda: str | int, 'Union[str, int]'),
        (lambda: list, 'list'),
        (lambda: List, 'List'),
        (lambda: list[int], 'list[int]'),
        (lambda: List[int], 'List[int]'),
        (lambda: list[dict[str, int]], 'list[dict[str, int]]'),
        (lambda: list[Union[str, int]], 'list[Union[str, int]]'),
        (lambda: list[str | int], 'list[Union[str, int]]'),
        (lambda: LoggedVar[int], 'LoggedVar[int]'),
        (lambda: LoggedVar[Dict[int, str]], 'LoggedVar[Dict[int, str]]'),
    ],
)
def test_display_as_type_310(value_gen, expected):
    value = value_gen()
    assert _repr.display_as_type(value) == expected


def test_lenient_issubclass():
    class A(str):
        pass

    assert lenient_issubclass(A, str) is True


@pytest.mark.skipif(sys.version_info < (3, 9), reason='generic aliases are not available in python < 3.9')
def test_lenient_issubclass_with_generic_aliases():
    from collections.abc import Mapping

    # should not raise an error here:
    assert lenient_issubclass(list[str], Mapping) is False


def test_lenient_issubclass_is_lenient():
    assert lenient_issubclass('a', 'a') is False


@pytest.mark.parametrize(
    'input_value,output',
    [
        ([], []),
        ([1, 1, 1, 2, 1, 2, 3, 2, 3, 1, 4, 2, 3, 1], [1, 2, 3, 4]),
        (['a', 'a', 'b', 'a', 'b', 'c', 'b', 'c', 'a'], ['a', 'b', 'c']),
    ],
)
def test_unique_list(input_value, output):
    assert unique_list(input_value) == output
    assert unique_list(unique_list(input_value)) == unique_list(input_value)


def test_value_items():
    v = ['a', 'b', 'c']
    vi = ValueItems(v, {0, -1})
    assert vi.is_excluded(2)
    assert [v_ for i, v_ in enumerate(v) if not vi.is_excluded(i)] == ['b']

    assert vi.is_included(2)
    assert [v_ for i, v_ in enumerate(v) if vi.is_included(i)] == ['a', 'c']

    v2 = {'a': v, 'b': {'a': 1, 'b': (1, 2)}, 'c': 1}

    vi = ValueItems(v2, {'a': {0, -1}, 'b': {'a': ..., 'b': -1}})

    assert not vi.is_excluded('a')
    assert vi.is_included('a')
    assert not vi.is_excluded('c')
    assert not vi.is_included('c')

    assert str(vi) == "{'a': {0, -1}, 'b': {'a': Ellipsis, 'b': -1}}"
    assert repr(vi) == "ValueItems({'a': {0, -1}, 'b': {'a': Ellipsis, 'b': -1}})"

    excluded = {k_: v_ for k_, v_ in v2.items() if not vi.is_excluded(k_)}
    assert excluded == {'a': v, 'b': {'a': 1, 'b': (1, 2)}, 'c': 1}

    included = {k_: v_ for k_, v_ in v2.items() if vi.is_included(k_)}
    assert included == {'a': v, 'b': {'a': 1, 'b': (1, 2)}}

    sub_v = included['a']
    sub_vi = ValueItems(sub_v, vi.for_element('a'))
    assert repr(sub_vi) == 'ValueItems({0: Ellipsis, 2: Ellipsis})'

    assert sub_vi.is_excluded(2)
    assert [v_ for i, v_ in enumerate(sub_v) if not sub_vi.is_excluded(i)] == ['b']

    assert sub_vi.is_included(2)
    assert [v_ for i, v_ in enumerate(sub_v) if sub_vi.is_included(i)] == ['a', 'c']


@pytest.mark.parametrize(
    'base,override,intersect,expected',
    [
        # Check in default (union) mode
        (..., ..., False, ...),
        (None, None, False, None),
        ({}, {}, False, {}),
        (..., None, False, ...),
        (None, ..., False, ...),
        (None, {}, False, {}),
        ({}, None, False, {}),
        (..., {}, False, {}),
        ({}, ..., False, ...),
        ({'a': None}, {'a': None}, False, {}),
        ({'a'}, ..., False, ...),
        ({'a'}, {}, False, {'a': ...}),
        ({'a'}, {'b'}, False, {'a': ..., 'b': ...}),
        ({'a': ...}, {'b': {'c'}}, False, {'a': ..., 'b': {'c': ...}}),
        ({'a': ...}, {'a': {'c'}}, False, {'a': {'c': ...}}),
        ({'a': {'c': ...}, 'b': {'d'}}, {'a': ...}, False, {'a': ..., 'b': {'d': ...}}),
        # Check in intersection mode
        (..., ..., True, ...),
        (None, None, True, None),
        ({}, {}, True, {}),
        (..., None, True, ...),
        (None, ..., True, ...),
        (None, {}, True, {}),
        ({}, None, True, {}),
        (..., {}, True, {}),
        ({}, ..., True, {}),
        ({'a': None}, {'a': None}, True, {}),
        ({'a'}, ..., True, {'a': ...}),
        ({'a'}, {}, True, {}),
        ({'a'}, {'b'}, True, {}),
        ({'a': ...}, {'b': {'c'}}, True, {}),
        ({'a': ...}, {'a': {'c'}}, True, {'a': {'c': ...}}),
        ({'a': {'c': ...}, 'b': {'d'}}, {'a': ...}, True, {'a': {'c': ...}}),
        # Check usage of `True` instead of `...`
        (..., True, False, True),
        (True, ..., False, ...),
        (True, None, False, True),
        ({'a': {'c': True}, 'b': {'d'}}, {'a': True}, False, {'a': True, 'b': {'d': ...}}),
    ],
)
def test_value_items_merge(base, override, intersect, expected):
    actual = ValueItems.merge(base, override, intersect=intersect)
    assert actual == expected


def test_value_items_error():
    with pytest.raises(TypeError) as e:
        ValueItems(1, (1, 2, 3))

    assert str(e.value) == "Unexpected type of exclude value <class 'tuple'>"


def test_is_new_type():
    new_type = NewType('new_type', str)
    new_new_type = NewType('new_new_type', new_type)
    assert is_new_type(new_type)
    assert is_new_type(new_new_type)
    assert not is_new_type(str)


def test_pretty():
    class MyTestModel(BaseModel):
        a: int = 1
        b: List[int] = [1, 2, 3]

    m = MyTestModel()
    assert m.__repr_name__() == 'MyTestModel'
    assert str(m) == 'a=1 b=[1, 2, 3]'
    assert repr(m) == 'MyTestModel(a=1, b=[1, 2, 3])'
    assert list(m.__pretty__(lambda x: f'fmt: {x!r}')) == [
        'MyTestModel(',
        1,
        'a=',
        'fmt: 1',
        ',',
        0,
        'b=',
        'fmt: [1, 2, 3]',
        ',',
        0,
        -1,
        ')',
    ]


def test_pretty_color():
    c = Color('red')
    assert str(c) == 'red'
    assert repr(c) == "Color('red', rgb=(255, 0, 0))"
    assert list(c.__pretty__(lambda x: f'fmt: {x!r}')) == [
        'Color(',
        1,
        "fmt: 'red'",
        ',',
        0,
        'rgb=',
        'fmt: (255, 0, 0)',
        ',',
        0,
        -1,
        ')',
    ]


@pytest.mark.skipif(not devtools, reason='devtools not installed')
def test_devtools_output():
    class MyTestModel(BaseModel):
        a: int = 1
        b: List[int] = [1, 2, 3]

    assert devtools.pformat(MyTestModel()) == 'MyTestModel(\n    a=1,\n    b=[1, 2, 3],\n)'


@pytest.mark.parametrize(
    'mapping, updating_mapping, expected_mapping, msg',
    [
        (
            {'key': {'inner_key': 0}},
            {'other_key': 1},
            {'key': {'inner_key': 0}, 'other_key': 1},
            'extra keys are inserted',
        ),
        (
            {'key': {'inner_key': 0}, 'other_key': 1},
            {'key': [1, 2, 3]},
            {'key': [1, 2, 3], 'other_key': 1},
            'values that can not be merged are updated',
        ),
        (
            {'key': {'inner_key': 0}},
            {'key': {'other_key': 1}},
            {'key': {'inner_key': 0, 'other_key': 1}},
            'values that have corresponding keys are merged',
        ),
        (
            {'key': {'inner_key': {'deep_key': 0}}},
            {'key': {'inner_key': {'other_deep_key': 1}}},
            {'key': {'inner_key': {'deep_key': 0, 'other_deep_key': 1}}},
            'deeply nested values that have corresponding keys are merged',
        ),
    ],
)
def test_deep_update(mapping, updating_mapping, expected_mapping, msg):
    assert deep_update(mapping, updating_mapping) == expected_mapping, msg


def test_deep_update_is_not_mutating():
    mapping = {'key': {'inner_key': {'deep_key': 1}}}
    updated_mapping = deep_update(mapping, {'key': {'inner_key': {'other_deep_key': 1}}})
    assert updated_mapping == {'key': {'inner_key': {'deep_key': 1, 'other_deep_key': 1}}}
    assert mapping == {'key': {'inner_key': {'deep_key': 1}}}


def test_undefined_repr():
    assert repr(Undefined) == 'PydanticUndefined'


def test_undefined_copy():
    assert copy(Undefined) is Undefined
    assert deepcopy(Undefined) is Undefined


def test_class_attribute():
    class Foo:
        attr = ClassAttribute('attr', 'foo')

    assert Foo.attr == 'foo'

    with pytest.raises(AttributeError, match="'attr' attribute of 'Foo' is class-only"):
        Foo().attr

    f = Foo()
    f.attr = 'not foo'
    assert f.attr == 'not foo'


def test_all_literal_values():
    L1 = Literal['1']
    assert all_literal_values(L1) == ['1']

    L2 = Literal['2']
    L12 = Literal[L1, L2]
    assert all_literal_values(L12) == IsList('1', '2', check_order=False)

    L312 = Literal['3', Literal[L1, L2]]
    assert all_literal_values(L312) == IsList('3', '1', '2', check_order=False)


@pytest.mark.parametrize(
    'obj',
    (1, 1.0, '1', b'1', int, None, test_all_literal_values, len, test_all_literal_values.__code__, lambda: ..., ...),
)
def test_smart_deepcopy_immutable_non_sequence(obj, mocker):
    # make sure deepcopy is not used
    # (other option will be to use obj.copy(), but this will produce error as none of given objects have this method)
    mocker.patch('pydantic._internal._utils.deepcopy', side_effect=RuntimeError)
    assert smart_deepcopy(obj) is deepcopy(obj) is obj


@pytest.mark.parametrize('empty_collection', (collection() for collection in BUILTIN_COLLECTIONS))
def test_smart_deepcopy_empty_collection(empty_collection, mocker):
    mocker.patch('pydantic._internal._utils.deepcopy', side_effect=RuntimeError)  # make sure deepcopy is not used
    if not isinstance(empty_collection, (tuple, frozenset)):  # empty tuple or frozenset are always the same object
        assert smart_deepcopy(empty_collection) is not empty_collection


@pytest.mark.parametrize(
    'collection', (c.fromkeys((1,)) if issubclass(c, dict) else c((1,)) for c in BUILTIN_COLLECTIONS)
)
def test_smart_deepcopy_collection(collection, mocker):
    expected_value = object()
    mocker.patch('pydantic._internal._utils.deepcopy', return_value=expected_value)
    assert smart_deepcopy(collection) is expected_value


@pytest.mark.parametrize('error', [TypeError, ValueError, RuntimeError])
def test_smart_deepcopy_error(error, mocker):
    class RaiseOnBooleanOperation(str):
        def __bool__(self):
            raise error('raised error')

    obj = RaiseOnBooleanOperation()
    expected_value = deepcopy(obj)
    assert smart_deepcopy(obj) == expected_value


T = TypeVar('T')


@pytest.mark.parametrize(
    'input_value,output_value',
    [
        (Annotated[int, 10] if Annotated else None, Annotated),
        (Callable[[], T][int], collections.abc.Callable),
        (Dict[str, int], dict),
        (List[str], list),
        (Union[int, str], Union),
        (int, None),
    ],
)
def test_get_origin(input_value, output_value):
    if input_value is None:
        pytest.skip('Skipping undefined hint for this python version')
    assert get_origin(input_value) is output_value


def test_all_identical():
    a, b = object(), object()
    c = [b]
    assert all_identical([a, b], [a, b]) is True
    assert all_identical([a, b], [a, b]) is True
    assert all_identical([a, b, b], [a, b, b]) is True
    assert all_identical([a, c, b], [a, c, b]) is True

    assert all_identical([], [a]) is False, 'Expected iterables with different lengths to evaluate to `False`'
    assert all_identical([a], []) is False, 'Expected iterables with different lengths to evaluate to `False`'
    assert (
        all_identical([a, [b], b], [a, [b], b]) is False
    ), 'New list objects are different objects and should therefore not be identical.'


def test_undefined_pickle():
    undefined2 = pickle.loads(pickle.dumps(Undefined))
    assert undefined2 is Undefined


def test_on_lower_camel_zero_length():
    assert to_lower_camel('') == ''


def test_on_lower_camel_one_length():
    assert to_lower_camel('a') == 'a'


def test_on_lower_camel_many_length():
    assert to_lower_camel('i_like_turtles') == 'iLikeTurtles'