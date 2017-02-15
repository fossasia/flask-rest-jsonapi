# -*- coding: utf-8 -*-

import inspect
from six import with_metaclass
import json
from copy import copy

from werkzeug.wrappers import Response
from flask import request, url_for, make_response
from flask.views import MethodViewType, MethodView
from marshmallow_jsonapi.exceptions import IncorrectTypeError
from marshmallow import ValidationError

from flask_rest_jsonapi.errors import jsonapi_errors
from flask_rest_jsonapi.querystring import QueryStringManager as QSManager
from flask_rest_jsonapi.pagination import add_pagination_links
from flask_rest_jsonapi.exceptions import ObjectNotFound, RelationNotFound, InvalidField, InvalidInclude, InvalidType, \
    BadRequest
from flask_rest_jsonapi.decorators import not_allowed_method, check_headers, check_method_requirements, add_headers
from flask_rest_jsonapi.schema import compute_schema
from flask_rest_jsonapi.data_layers.base import BaseDataLayer
from flask_rest_jsonapi.data_layers.alchemy import SqlalchemyDataLayer


class ResourceMeta(MethodViewType):

    def __init__(cls, name, bases, nmspc):
        super(ResourceMeta, cls).__init__(name, bases, nmspc)
        meta = nmspc.get('Meta')

        # compute data_layer
        data_layer = None

        alternative_data_layer_cls = getattr(meta, 'data_layer', None)
        if alternative_data_layer_cls is not None and BaseDataLayer not in inspect.getmro(alternative_data_layer_cls):
            raise Exception("You must provide a data layer class inherited from BaseDataLayer in %s resource" % name)

        if nmspc.get('data_layer_kwargs') is not None:
            if not isinstance(nmspc['data_layer_kwargs'], dict):
                raise Exception("You must provide data layer information as dictionary in %s resource" % name)
            else:
                data_layer_cls = getattr(meta, 'data_layer', SqlalchemyDataLayer)
                data_layer_kwargs = nmspc.get('data_layer_kwargs', dict())
                data_layer = type('%sDataLayer' % name, (data_layer_cls, ), dict())(**data_layer_kwargs)
                data_layer.configure(meta)

        if data_layer is not None:
            data_layer.resource = cls
            cls.data_layer = data_layer

        # disable access to methods according to meta options
        if meta is not None:
            not_allowed_methods = getattr(meta, 'not_allowed_methods', [])
            for method in not_allowed_methods:
                if hasattr(cls, method.lower()):
                    setattr(cls, method.lower(), not_allowed_method(getattr(cls, method.lower())))

        # set meta information as opts of the resource class
        cls.opts = meta


class ResourceListMeta(ResourceMeta):

    def __init__(cls, name, bases, nmspc):
        super(ResourceListMeta, cls).__init__(name, bases, nmspc)
        meta = nmspc.get('Meta')

        if meta is not None:
            get_decorators = getattr(meta, 'get_decorators', [])
            post_decorators = getattr(meta, 'post_decorators', [])

            for get_decorator in get_decorators:
                cls.get = get_decorator(cls.get)

            for post_decorator in post_decorators:
                cls.post = post_decorator(cls.post)


class ResourceDetailMeta(ResourceMeta):

    def __init__(cls, name, bases, nmspc):
        super(ResourceDetailMeta, cls).__init__(name, bases, nmspc)
        meta = nmspc.get('Meta')

        if meta is not None:
            get_decorators = getattr(meta, 'get_decorators', [])
            patch_decorators = getattr(meta, 'patch_decorators', [])
            delete_decorators = getattr(meta, 'delete_decorators', [])

            for get_decorator in get_decorators:
                cls.get = get_decorator(cls.get)

            for patch_decorator in patch_decorators:
                cls.patch = patch_decorator(cls.patch)

            for delete_decorator in delete_decorators:
                cls.delete = delete_decorator(cls.delete)


class ResourceRelationshipMeta(ResourceMeta):

    def __init__(cls, name, bases, nmspc):
        super(ResourceRelationshipMeta, cls).__init__(name, bases, nmspc)
        meta = nmspc.get('Meta')

        if meta is not None:
            get_decorators = getattr(meta, 'get_decorators', [])
            post_decorators = getattr(meta, 'post_decorators', [])
            patch_decorators = getattr(meta, 'patch_decorators', [])
            delete_decorators = getattr(meta, 'delete_decorators', [])

            for get_decorator in get_decorators:
                cls.get = get_decorator(cls.get)

            for post_decorator in post_decorators:
                cls.post = post_decorator(cls.post)

            for patch_decorator in patch_decorators:
                cls.patch = patch_decorator(cls.patch)

            for delete_decorator in delete_decorators:
                cls.delete = delete_decorator(cls.delete)


class Resource(MethodView):

    decorators = (check_headers, add_headers)

    def dispatch_request(self, *args, **kwargs):
        meth = getattr(self, request.method.lower(), None)
        if meth is None and request.method == 'HEAD':
            meth = getattr(self, 'get', None)
        assert meth is not None, 'Unimplemented method %r' % request.method

        resp = meth(*args, **kwargs)

        if isinstance(resp, Response):
            return resp

        if not isinstance(resp, tuple):
            return make_response(json.dumps(resp))

        try:
            data, status_code, headers = resp
        except ValueError:
            pass

        try:
            data, status_code = resp
            headers = {}
        except ValueError:
            pass

        return make_response(json.dumps(data), status_code, headers)


class ResourceList(with_metaclass(ResourceListMeta, Resource)):

    @check_method_requirements
    def get(self, *args, **kwargs):
        """Retrieve a collection of objects
        """
        qs = QSManager(request.args)

        object_count, objects = self.data_layer.get_collection(qs, **kwargs)

        schema_kwargs = getattr(self.opts, 'schema_get_kwargs', dict())
        schema_kwargs.update({'many': True})
        try:
            schema = compute_schema(self.schema, schema_kwargs, qs, None)
        except InvalidField as e:
            return jsonapi_errors([e.to_dict()]), e.status

        result = schema.dump(objects)

        endpoint_kwargs = request.view_args if getattr(self.opts, 'include_endpoint_kwargs', None) is True else dict()
        add_pagination_links(result.data,
                             object_count,
                             qs,
                             url_for(self.endpoint, **endpoint_kwargs))

        return result.data

    @check_method_requirements
    def post(self, *args, **kwargs):
        """Create an object
        """
        json_data = request.get_json()

        schema_kwargs = getattr(self.opts, 'schema_post_kwargs', dict())
        schema = self.schema(**schema_kwargs)
        try:
            data, errors = schema.load(json_data)
        except IncorrectTypeError as e:
            errors = e.messages
            for error in errors['errors']:
                error['status'] = '409'
                error['title'] = "Incorrect type"
            return errors, 409
        except ValidationError as e:
            errors = e.messages
            for message in errors['errors']:
                message['status'] = '422'
                message['title'] = "Validation error"
            return errors, 422

        if errors:
            for error in errors['errors']:
                error['status'] = "422"
                error['title'] = "Validation error"
            return errors, 422

        obj = self.data_layer.create_object(data, **kwargs)

        return schema.dump(obj).data, 201


class ResourceDetail(with_metaclass(ResourceDetailMeta, Resource)):

    @check_method_requirements
    def get(self, *args, **kwargs):
        """Get object details
        """
        try:
            obj = self.data_layer.get_object(**kwargs)
        except ObjectNotFound as e:
            return jsonapi_errors([e.to_dict()]), e.status

        qs = QSManager(request.args)
        try:
            schema = compute_schema(self.schema,
                                    getattr(self.opts, 'schema_get_kwargs', dict()),
                                    qs,
                                    qs.include)
        except (InvalidField, InvalidInclude) as e:
            return jsonapi_errors([e.to_dict()]), e.status

        result = schema.dump(obj)
        return result.data

    @check_method_requirements
    def patch(self, *args, **kwargs):
        """Update an object
        """
        json_data = request.get_json()

        schema_kwargs = getattr(self.opts, 'schema_patch_kwargs', dict())
        schema_kwargs.pop('partial', None)
        schema = self.schema(partial=True, **schema_kwargs)
        try:
            data, errors = schema.load(json_data)
        except IncorrectTypeError as e:
            errors = e.messages
            for error in errors['errors']:
                error['status'] = '409'
                error['title'] = "Incorrect type"
            return errors, 409
        except ValidationError as e:
            errors = e.messages
            for message in errors['errors']:
                message['status'] = '422'
                message['title'] = "Validation error"
            return errors, 422

        if errors:
            for error in errors['errors']:
                error['status'] = "422"
                error['title'] = "Validation error"
            return errors, 422

        try:
            if 'id' not in json_data['data']:
                raise BadRequest('/data/id', 'Missing id in "data" node')
            if json_data['data']['id'] != kwargs[self.data_layer.url_field]:
                raise BadRequest('/data/id', 'Value of id does not match the resource identifier in url')
        except BadRequest as e:
            return jsonapi_errors([e.to_dict()]), e.status

        try:
            obj = self.data_layer.get_object(**kwargs)
        except ObjectNotFound as e:
            return jsonapi_errors([e.to_dict()]), e.status

        self.data_layer.update_object(obj, data, **kwargs)

        result = schema.dump(obj)

        return result.data

    @check_method_requirements
    def delete(self, *args, **kwargs):
        """Delete an object
        """
        try:
            obj = self.data_layer.get_object(**kwargs)
        except ObjectNotFound as e:
            return jsonapi_errors([e.to_dict()]), e.status

        self.data_layer.delete_object(obj, **kwargs)

        return '', 204


class Relationship(with_metaclass(ResourceRelationshipMeta, Resource)):

    @check_method_requirements
    def get(self, *args, **kwargs):
        """Get a relationship details
        """
        related_id_field = getattr(self.opts, 'related_id_field', 'id')
        try:
            obj, data = self.data_layer.get_relation(self.related_type_, related_id_field, **kwargs)
        except (RelationNotFound, ObjectNotFound) as e:
            return jsonapi_errors([e.to_dict()]), e.status

        related_endpoint_kwargs = kwargs
        if hasattr(self.opts, 'endpoint_kwargs'):
            for key, value in copy(self.opts.endpoint_kwargs).items():
                tmp_endpoint_kwargs_value = obj
                for field in value.split('.'):
                    tmp_endpoint_kwargs_value = getattr(tmp_endpoint_kwargs_value, field)
                endpoint_kwargs_value = tmp_endpoint_kwargs_value
                self.opts.endpoint_kwargs[key] = endpoint_kwargs_value
            related_endpoint_kwargs = self.opts.endpoint_kwargs

        result = {'links': {'self': url_for(self.endpoint, **kwargs),
                            'related': url_for(self.related_endpoint, **related_endpoint_kwargs)},
                  'data': data}

        qs = QSManager(request.args)
        if qs.include:
            try:
                schema = compute_schema(self.schema, dict(), qs, qs.include)
            except (InvalidField, InvalidInclude) as e:
                return jsonapi_errors([e.to_dict()]), e.status

            serialized_obj = schema.dump(obj)
            result['included'] = serialized_obj.data['included']

        return result

    @check_method_requirements
    def post(self, *args, **kwargs):
        """Add / create relationship(s)
        """
        json_data = request.get_json()

        type_ = self.schema.opts.type_

        try:
            if 'data' not in json_data:
                raise BadRequest('/data', 'You must provide data with a "data" route node')
            if not isinstance(json_data.get('data'), list):
                raise BadRequest('/data', 'You must provide data as list')
            for obj in json_data['data']:
                if 'type' not in obj:
                    raise BadRequest('/data/type', 'Missing type in "data" node')
                if 'id' not in obj:
                    raise BadRequest('/data/id', 'Missing id in "data" node')
                if obj['type'] != type_:
                    raise InvalidType('/data/type', 'The type provided does not match the resource type')
        except (BadRequest, InvalidType) as e:
            return jsonapi_errors([e.to_dict()]), e.status

        related_id_field = getattr(self.opts, 'related_id_field', 'id')
        try:
            self.data_layer.create_relation(json_data, related_id_field, **kwargs)
        except (RelationNotFound, ObjectNotFound) as e:
            return jsonapi_errors([e.to_dict()]), e.status

        return ''

    @check_method_requirements
    def patch(self, *args, **kwargs):
        """Update a relationship
        """
        json_data = request.get_json()

        type_ = self.schema.opts.type_

        try:
            if 'data' not in json_data:
                raise BadRequest('/data', 'You must provide data with a "data" route node')
            if isinstance(json_data['data'], dict):
                if 'type' not in json_data['data']:
                    raise BadRequest('/data/type', 'Missing type in "data" node')
                if 'id' not in json_data['data']:
                    raise BadRequest('/data/id', 'Missing id in "data" node')
                if json_data['data']['type'] != type_:
                    raise InvalidType('/data/type', 'The type field does not match the resource type')
            if isinstance(json_data['data'], list):
                for obj in json_data['data']:
                    if 'type' not in obj:
                        raise BadRequest('/data/type', 'Missing type in "data" node')
                    if 'id' not in obj:
                        raise BadRequest('/data/id', 'Missing id in "data" node')
                    if obj['type'] != self.type_:
                        raise InvalidType('/data/type', 'The type provided does not match the resource type')
        except (BadRequest, InvalidType) as e:
            return jsonapi_errors([e.to_dict()]), e.status

        related_id_field = getattr(self.opts, 'related_id_field', 'id')
        try:
            self.data_layer.update_relation(json_data, related_id_field, **kwargs)
        except (RelationNotFound, ObjectNotFound) as e:
            return jsonapi_errors([e.to_dict()]), e.status

        return ''

    @check_method_requirements
    def delete(self, *args, **kwargs):
        """Delete relationship(s)
        """
        json_data = request.get_json()

        type_ = self.schema.opts.type_

        try:
            if 'data' not in json_data:
                raise BadRequest('/data', 'You must provide data with a "data" route node')
            if not isinstance(json_data.get('data'), list):
                raise BadRequest('/data', 'You must provide data as list')
            for obj in json_data['data']:
                if 'type' not in obj:
                    raise BadRequest('/data/type', 'Missing type in "data" node')
                if 'id' not in obj:
                    raise BadRequest('/data/id', 'Missing id in "data" node')
                if obj['type'] != type_:
                    raise InvalidType('/data/type', 'The type provided does not match the resource type')
        except (BadRequest, InvalidType) as e:
            return jsonapi_errors([e.to_dict()]), e.status

        related_id_field = getattr(self.opts, 'related_id_field', 'id')
        try:
            self.data_layer.delete_relation(json_data, related_id_field, **kwargs)
        except RelationNotFound as e:
            return jsonapi_errors([e.to_dict()]), e.status

        return ''
