# Copyright 2024 The Flax Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import builtins
import dataclasses
from flax.typing import Key, PathParts
import typing as tp

if tp.TYPE_CHECKING:
  ellipsis = builtins.ellipsis
else:
  ellipsis = tp.Any

Predicate = tp.Callable[[PathParts, tp.Any], bool]

FilterLiteral = tp.Union[type, str, Predicate, bool, ellipsis, None]
Filter = tp.Union[FilterLiteral, tuple['Filter', ...], list['Filter']]


@tp.runtime_checkable
class _HasTag(tp.Protocol):
  tag: str

@tp.runtime_checkable
class _HasType(tp.Protocol):
  type: type


def to_predicate(filter: Filter) -> Predicate:
  """Converts a Filter to a predicate function.
  See `Using Filters <https://flax.readthedocs.io/en/latest/nnx/filters_guide.html>`__.
  """

  if isinstance(filter, str):
    return WithTag(filter)
  elif isinstance(filter, type):
    return OfType(filter)
  elif isinstance(filter, bool):
    if filter:
      return Everything()
    else:
      return Nothing()
  elif filter is Ellipsis:
    return Everything()
  elif filter is None:
    return Nothing()
  elif callable(filter):
    return filter
  elif isinstance(filter, (list, tuple)):
    return Any(*filter)
  else:
    raise TypeError(f'Invalid collection filter: {filter:!r}. ')


@dataclasses.dataclass(frozen=True)
class WithTag:
  tag: str

  def __call__(self, path: PathParts, x: tp.Any):
    return isinstance(x, _HasTag) and x.tag == self.tag

  def __repr__(self):
    return f'WithTag({self.tag!r})'


@dataclasses.dataclass(frozen=True)
class PathContains:
  key: Key

  def __call__(self, path: PathParts, x: tp.Any):
    return self.key in path

  def __repr__(self):
    return f'PathContains({self.key!r})'


@dataclasses.dataclass(frozen=True)
class OfType:
  type: type

  def __call__(self, path: PathParts, x: tp.Any):
    return isinstance(x, self.type) or (
      isinstance(x, _HasType) and issubclass(x.type, self.type)
    )

  def __repr__(self):
    return f'OfType({self.type!r})'


class Any:
  def __init__(self, *filters: Filter):
    self.predicates = tuple(
      to_predicate(collection_filter) for collection_filter in filters
    )

  def __call__(self, path: PathParts, x: tp.Any):
    return any(predicate(path, x) for predicate in self.predicates)

  def __repr__(self):
    return f'Any({", ".join(map(repr, self.predicates))})'

  def __eq__(self, other):
    return isinstance(other, Any) and self.predicates == other.predicates

  def __hash__(self):
    return hash(self.predicates)


class All:
  def __init__(self, *filters: Filter):
    self.predicates = tuple(
      to_predicate(collection_filter) for collection_filter in filters
    )

  def __call__(self, path: PathParts, x: tp.Any):
    return all(predicate(path, x) for predicate in self.predicates)

  def __repr__(self):
    return f'All({", ".join(map(repr, self.predicates))})'

  def __eq__(self, other):
    return isinstance(other, All) and self.predicates == other.predicates

  def __hash__(self):
    return hash(self.predicates)


class Not:
  def __init__(self, collection_filter: Filter, /):
    self.predicate = to_predicate(collection_filter)

  def __call__(self, path: PathParts, x: tp.Any):
    return not self.predicate(path, x)

  def __repr__(self):
    return f'Not({self.predicate!r})'

  def __eq__(self, other):
    return isinstance(other, Not) and self.predicate == other.predicate

  def __hash__(self):
    return hash(self.predicate)


class Everything:
  def __call__(self, path: PathParts, x: tp.Any):
    return True

  def __repr__(self):
    return 'Everything()'

  def __eq__(self, other):
    return isinstance(other, Everything)

  def __hash__(self):
    return hash(Everything)


class Nothing:
  def __call__(self, path: PathParts, x: tp.Any):
    return False

  def __repr__(self):
    return 'Nothing()'

  def __eq__(self, other):
    return isinstance(other, Nothing)

  def __hash__(self):
    return hash(Nothing)
