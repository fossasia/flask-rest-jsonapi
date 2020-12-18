# -*- coding: utf-8 -*-

import json

from flask_rest_jsonapi.exceptions import BadRequest, InvalidFilters, InvalidSort
from flask_rest_jsonapi.schema import get_model_field, get_relationships
from flask import current_app


class QueryStringManager(object):
    """Querystring parser according to jsonapi reference
    """

    MANAGED_KEYS = (
        'filter',
        'page',
        'fields',
        'sort',
        'include'
    )

    def __init__(self, querystring, schema):
        """Initialization instance

        :param dict querystring: query string dict from request.args
        """
        if not isinstance(querystring, dict):
            raise ValueError('QueryStringManager require a dict-like object query_string parameter')

        self.qs = querystring
        self.schema = schema

    def _get_key_values(self, name):
        """Return a dict containing key / values items for a given key, used for items like filters, page, etc.

        :param str name: name of the querystring parameter
        :return dict: a dict of key / values items
        """
        results = {}

        for key, value in self.qs.items():
            try:
                if not key.startswith(name):
                    continue

                key_start = key.index('[') + 1
                key_end = key.index(']')
                item_key = key[key_start:key_end]

                if ',' in value:
                    item_value = value.split(',')
                else:
                    item_value = value
                results.update({item_key: item_value})
            except Exception:
                raise BadRequest({'parameter': key}, "Parse error")

        return results

    @property
    def querystring(self):
        """Return original querystring but containing only managed keys

        :return dict: dict of managed querystring parameter
        """
        return {key: value for (key, value) in self.qs.items() if key.startswith(self.MANAGED_KEYS)}

    @property
    def filters(self):
        """Return filters from query string.

        :return list: filter information
        """
        filters = self.qs.get('filter')
        if filters is not None:
            try:
                filters = json.loads(filters)
            except (ValueError, TypeError):
                raise InvalidFilters("Parse error")

        return filters

    @property
    def pagination(self):
        """Return all page parameters as a dict.

        :return dict: a dict of pagination information

        To allow multiples strategies, all parameters starting with `page` will be included. e.g::

            {
                "number": '25',
                "size": '150',
            }

        Example with number strategy::

            >>> query_string = {'page[number]': '25', 'page[size]': '10'}
            >>> parsed_query.pagination
            {'number': '25', 'size': '10'}
        """
        # check values type
        result = self._get_key_values('page')
        for key, value in result.items():
            if key not in ('number', 'size'):
                raise BadRequest({'parameter': 'page'}, "{} is not a valid parameter of pagination".format(key))
            try:
                int(value)
            except ValueError:
                raise BadRequest({'parameter': 'page[{}]'.format(key)}, "Parse error")

        return result

    @property
    def fields(self):
        """Return fields wanted by client.

        :return dict: a dict of sparse fieldsets information

        Return value will be a dict containing all fields by resource, for example::

            {
                "user": ['name', 'email'],
            }

        """
        result = self._get_key_values('fields')
        for key, value in result.items():
            if not isinstance(value, list):
                if current_app.config.get('DASHERIZE_API') == True:
                    result[key] = [value.replace('-', '_')]
                else:
                    result[key] = [value]
        return result

    @property
    def sorting(self):
        """Return fields to sort by including sort name for SQLAlchemy and row
        sort parameter for other ORMs

        :return list: a list of sorting information

        Example of return value::

            [
                {'field': 'created_at', 'order': 'desc'},
            ]

        """
        if self.qs.get('sort'):
            sorting_results = []
            for sort_field in self.qs['sort'].split(','):
                if current_app.config.get('DASHERIZE_API') == True:
                    field = sort_field[0].replace('-', '') + sort_field[1:].replace('-', '_')
                else:
                    field = sort_field[0].replace('-', '') + sort_field[1:]
                def check_schema_field(schema, field):
                    if field not in schema._declared_fields:
                        raise InvalidSort("{} has no attribute {}".format(schema.__name__, field))
                    if field in get_relationships(schema).values():
                        raise InvalidSort("You can't sort on {} because it is a relationship field".format(field))
                schema = self.schema
                relation = None
                if '.' in field:
                    relation, field = field.split('.')
                    if relation not in get_relationships(self.schema).values():
                        raise InvalidSort("{} has no relationship {}".format(self.schema.__name__, relation))
                    relation_field = self.schema._declared_fields[relation]
                    relation = get_model_field(self.schema, relation)
                    schema = relation_field.schema.__class__

                check_schema_field(schema, field)
                field = get_model_field(schema, field)
                order = 'desc' if sort_field.startswith('-') else 'asc'
                sorting_results.append({'field': field, 'order': order, 'relation': relation})
            return sorting_results

        return []

    @property
    def include(self):
        """Return fields to include

        :return list: a list of include information
        """
        include_param = self.qs.get('include')
        if include_param:
            param_results = []
            for param in include_param.split(','):
                if current_app.config.get('DASHERIZE_API') == True:
                    param = param.replace('-', '_')
                param_results.append(param)
            return param_results
        return []
