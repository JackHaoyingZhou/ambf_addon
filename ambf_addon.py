# Author: Adnan Munawar
# Email: amunawar@wpi.edu
# Lab: aimlab.wpi.edu

bl_info = {
    "name": "Asynchronous Multi-Body Framework (AMBF) Config Creator",
    "author": "Adnan Munawar",
    "version": (0, 1),
    "blender": (2, 79, 0),
    "location": "View3D > Add > Mesh > AMBF",
    "description": "Helps Generate AMBF Config File and Saves both High and Low Resolution(Collision) Meshes",
    "warning": "",
    "wiki_url": "https://github.com/WPI-AIM/ambf_addon",
    "category": "AMBF",
    }

import bpy
import math
import yaml
import os
from pathlib import Path
import mathutils
from enum import Enum
from collections import OrderedDict, Counter
from datetime import datetime


# https://stackoverflow.com/questions/31605131/dumping-a-dictionary-to-a-yaml-file-while-preserving-order/31609484
def represent_dictionary_order(self, dict_data):
    return self.represent_mapping('tag:yaml.org,2002:map', dict_data.items())


def setup_yaml():
    yaml.add_representer(OrderedDict, represent_dictionary_order)


# Enum Class for Mesh Type
class MeshType(Enum):
    meshSTL = 0
    meshOBJ = 1
    mesh3DS = 2
    meshPLY = 3


def get_extension(val):
    if val == MeshType.meshSTL.value:
        extension = '.STL'
    elif val == MeshType.meshOBJ.value:
        extension = '.OBJ'
    elif val == MeshType.mesh3DS.value:
        extension = '.3DS'
    elif val == MeshType.meshPLY.value:
        extension = '.PLY'
    else:
        extension = None

    return extension


def skew_mat(v):
    m = mathutils.Matrix.Identity(3)
    m.Identity(3)
    m[0][0] = 0
    m[0][1] = -v.z
    m[0][2] = v.y
    m[1][0] = v.z
    m[1][1] = 0
    m[1][2] = -v.x
    m[2][0] = -v.y
    m[2][1] = v.x
    m[2][2] = 0

    return m


def vec_norm(v):
    return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)


def round_vec(v):
    for i in range(0, 3):
        v[i] = round(v[i], 3)
    return v


# https://math.stackexchange.com/questions/180418/calculate-rotation-matrix-to-align-vector-a-to-vector-b-in-3d/897677#897677
def rot_matrix_from_vecs(v1, v2):
    out = mathutils.Matrix.Identity(3)
    vcross = v1.cross(v2)
    vdot = v1.dot(v2)
    rot_angle = v1.angle(v2)
    if 1.0 - vdot < 0.1:
        return out
    elif 1.0 + vdot < 0.1:
        # This is a more involved case, find out the orthogonal vector to vecA
        nx = mathutils.Vector([1, 0, 0])
        temp_ang = v1.angle(nx)
        if 0.1 < abs(temp_ang) < 3.13:
            axis = v1.cross(nx)
            out = out.Rotation(rot_angle, 3, axis)
        else:
            ny = mathutils.Vector([0, 1, 0])
            axis = v1.cross(ny)
            out = out.Rotation(rot_angle, 3, axis)
    else:
        skew_v = skew_mat(vcross)
        out = mathutils.Matrix.Identity(3) + skew_v + skew_v * skew_v * ((1 - vdot) / (vec_norm(vcross) ** 2))
    return out


# Get rotation matrix to represent rotation between two vectors
# Brute force implementation
def get_rot_mat_from_vecs(vecA, vecB):
    # Angle between two axis
    angle = vecA.angle(vecB)
    # Axis of rotation between child's joints axis and constraint_axis
    if abs(angle) <= 0.1:
        # Doesn't matter which axis we chose, the rot mat is going to be identity
        # as angle is almost 0
        axis = mathutils.Vector([0, 1, 0])
    elif abs(angle) >= 3.13:
        # This is a more involved case, find out the orthogonal vector to vecA
        nx = mathutils.Vector([1, 0, 0])
        temp_ang = vecA.angle(nx)
        if 0.1 < abs(temp_ang) < 3.13:
            axis = vecA.cross(nx)
        else:
            ny = mathutils.Vector([0, 1, 0])
            axis = vecA.cross(ny)
    else:
        axis = vecA.cross(vecB)

    mat = mathutils.Matrix()
    # Rotation matrix representing the above angular offset
    rot_mat = mat.Rotation(angle, 4, axis)
    return rot_mat, angle


# Global Variables
class CommonConfig:
    # Since there isn't a convenient way of defining parallel linkages (hence detached joints) due to the
    # limit on 1 parent per body. We use kind of a hack. This prefix is what we we search for it we find an
    # empty body with the mentioned prefix.
    detached_joint_prefix = ['redundant', 'Redundant', 'REDUNDANT', 'detached', 'Detached', 'DETACHED']
    namespace = ''
    num_collision_groups = 20
    # Some properties don't exist in Blender are supported in AMBF. If an AMBF file is loaded
    # and then resaved, we can capture the extra properties of bodies and joints and take
    # them into consideration before re saving the AMBF File so we don't reset those values
    loaded_body_map = {}
    loaded_joint_map = {}


def update_global_namespace(context):
    CommonConfig.namespace = context.scene.ambf_namespace
    if CommonConfig.namespace[-1] != '/':
        print('WARNING, MULTI-BODY NAMESPACE SHOULD END WITH \'/\'')
        CommonConfig.namespace += '/'
        context.scene.ambf_namespace += CommonConfig.namespace


def set_global_namespace(context, namespace):
    CommonConfig.namespace = namespace
    if CommonConfig.namespace[-1] != '/':
        print('WARNING, MULTI-BODY NAMESPACE SHOULD END WITH \'/\'')
        CommonConfig.namespace += '/'
    context.scene.ambf_namespace = CommonConfig.namespace


def get_body_namespace(fullname):
    last_occurance = fullname.rfind('/')
    _body_namespace = ''
    if last_occurance >= 0:
        # This means that the name contains a namespace
        _body_namespace = fullname[0:last_occurance+1]
    return _body_namespace


def remove_namespace_prefix(full_name):
    last_occurance = full_name.rfind('/')
    if last_occurance > 0:
        # Body name contains a namespace
        _name = full_name[last_occurance+1:]
    else:
        # Body name doesn't have a namespace
        _name = full_name
    return _name


def replace_dot_from_obj_names(char_subs = '_'):
    for obj in bpy.data.objects:
        obj.name = obj.name.replace('.', char_subs)


def compare_body_namespace_with_global(fullname):
    last_occurance = fullname.rfind('/')
    _is_namespace_same = False
    _body_namespace = ''
    _name = ''
    if last_occurance >= 0:
        # This means that the name contains a namespace
        _body_namespace = fullname[0:last_occurance+1]
        _name = fullname[last_occurance+1:]

        if CommonConfig.namespace == _body_namespace:
            # The CommonConfig namespace is the same as the body namespace
            _is_namespace_same = True
        else:
            # The CommonConfig namespace is different form body namespace
            _is_namespace_same = False

    else:
        # The body's name does not contain and namespace
        _is_namespace_same = False

    # print("FULLNAME: %s, BODY: %s, NAMESPACE: %s NAMESPACE_MATCHED: %d" %
    # (fullname, _name, _body_namespace, _is_namespace_same))
    return _is_namespace_same


def add_namespace_prefix(name):
    return CommonConfig.namespace + name


def get_grand_parent(body):
    grand_parent = body
    while grand_parent.parent is not None:
        grand_parent = grand_parent.parent
    return grand_parent


def downward_tree_pass(body, _heirarichal_bodies_list, _added_bodies_list):
    if body is None or _added_bodies_list[body] is True:
        return

    else:
        # print('DOWNWARD TREE PASS: ', body.name)
        _heirarichal_bodies_list.append(body)
        _added_bodies_list[body] = True

        for child in body.children:
            downward_tree_pass(child, _heirarichal_bodies_list, _added_bodies_list)


def populate_heirarchial_tree():
    # Create a dict with {body, added_flag} elements
    # The added_flag is to check if the body has already
    # been added
    _added_bodies_list = {}
    _heirarchial_bodies_list = []

    for obj in bpy.data.objects:
        _added_bodies_list[obj] = False

    for obj in bpy.data.objects:
        grand_parent = get_grand_parent(obj)
        # print('CALLING DOWNWARD TREE PASS FOR: ', grand_parent.name)
        downward_tree_pass(grand_parent, _heirarchial_bodies_list, _added_bodies_list)

    for body in _heirarchial_bodies_list:
        print(body.name, "--->",)

    return _heirarchial_bodies_list


# Courtesy: https://stackoverflow.com/questions/5914627/prepend-line-to-beginning-of-a-file
def prepend_comment_to_file(filename, comment):
    temp_filename = filename + '.tmp'
    with open(filename,'r') as f:
        with open(temp_filename, 'w') as f2:
            f2.write(comment)
            f2.write(f.read())
    os.rename(temp_filename, filename)


def select_all_objects(select=True):
    # First deselect all objects
    for obj in bpy.data.objects:
        obj.select = select


# For shapes such as Cylinder, Cone and Ellipse, this function returns
# the major axis by comparing the dimensions of the bounding box
def get_major_axis(dims):
    d = dims
    axis = {0: 'x', 1: 'y', 2: 'z'}
    sum_diff = [abs(d[0] - d[1]) + abs(d[0] - d[2]),
                abs(d[1] - d[0]) + abs(d[1] - d[2]),
                abs(d[2] - d[0]) + abs(d[2] - d[1])]
    # If the bounds are equal, choose the z axis
    if sum_diff[0] == sum_diff[1] and sum_diff[1] == sum_diff[2]:
        axis_idx = 2
    else:
        axis_idx = sum_diff.index(max(sum_diff))

    return axis[axis_idx], axis_idx


# For shapes such as Cylinder, Cone and Ellipse, this function returns
# the median axis (not-major and non-minor or the middle axis) by comparing
# the dimensions of the bounding box
def get_median_axis(dims):
    axis = {0: 'x', 1: 'y', 2: 'z'}
    maj_ax, maj_ax_idx = get_major_axis(dims)
    min_ax, min_ax_idx = get_minor_axis(dims)
    med_axis_idx = [1, 1, 1]
    med_axis_idx[maj_ax_idx] = 0
    med_axis_idx[min_ax_idx] = 0
    axis_idx = med_axis_idx.index(max(med_axis_idx))

    return axis[axis_idx], axis_idx


# For shapes such as Cylinder, Cone and Ellipse, this function returns
# the minor axis by comparing the dimensions of the bounding box
def get_minor_axis(dims):
    d = dims
    axis = {0: 'x', 1: 'y', 2: 'z'}
    sum_diff = [abs(d[0] - d[1]) + abs(d[0] - d[2]),
                abs(d[1] - d[0]) + abs(d[1] - d[2]),
                abs(d[2] - d[0]) + abs(d[2] - d[1])]
    max_idx = sum_diff.index(max(sum_diff))
    min_idx = sum_diff.index(min(sum_diff))
    sort_idx = [1, 1, 1]
    sort_idx[max_idx] = 0
    sort_idx[min_idx] = 0
    median_idx = sort_idx.index(max(sort_idx))
    return axis[median_idx], median_idx


# Body Template for the some commonly used of afBody's data
class BodyTemplate:
    def __init__(self):
        self._ambf_data = OrderedDict()
        self._ambf_data['name'] = ""
        self._ambf_data['mesh'] = ""
        self._ambf_data['mass'] = 0.0
        self._ambf_data['inertia'] = {'ix': 0.0, 'iy': 0.0, 'iz': 0.0}
        self._ambf_data['collision margin'] = 0.001
        self._ambf_data['scale'] = 1.0
        self._ambf_data['location'] = {'position': {'x': 0, 'y': 0, 'z': 0},
                                       'orientation': {'r': 0, 'p': 0, 'y': 0}}
        self._ambf_data['inertial offset'] = {'position': {'x': 0, 'y': 0, 'z': 0},
                                              'orientation': {'r': 0, 'p': 0, 'y': 0}}

        # self._ambf_data['controller'] = {'linear': {'P': 1000, 'I': 0, 'D': 1},
        #                                  'angular': {'P': 1000, 'I': 0, 'D': 1}}
        self._ambf_data['color'] = 'random'
        # Transform of Child Rel to Joint, which in inverse of t_c_j
        self.t_j_c = mathutils.Matrix()


# Joint Template for the some commonly used of afJoint's data
class JointTemplate:
    def __init__(self):
        self._ambf_data = OrderedDict()
        self._ambf_data['name'] = ''
        self._ambf_data['parent'] = ''
        self._ambf_data['child'] = ''
        self._ambf_data['parent axis'] = {'x': 0, 'y': 0.0, 'z': 1.0}
        self._ambf_data['parent pivot'] = {'x': 0, 'y': 0.0, 'z': 0}
        self._ambf_data['child axis'] = {'x': 0, 'y': 0.0, 'z': 1.0}
        self._ambf_data['child pivot'] = {'x': 0, 'y': 0.0, 'z': 0}
        self._ambf_data['joint limits'] = {'low': -1.2, 'high': 1.2}
        self._ambf_data['controller'] = {'P': 1000, 'I': 0, 'D': 1}


class AMBF_OT_generate_ambf_file(bpy.types.Operator):
    """Tooltip"""
    bl_idname = "ambf.add_generate_ambf_file"
    bl_label = "Write AMBF Description File (ADF)"
    bl_description = "This generated the AMBF Config file in the location and filename specified in the field" \
                     " above"

    def __init__(self):
        self._body_names_list = []
        self._joint_names_list = []
        self.body_name_prefix = 'BODY '
        self.joint_name_prefix = 'JOINT '
        self._ambf_yaml = None
        self._context = None

    def execute(self, context):
        self._context = context
        self.generate_ambf_yaml()
        return {'FINISHED'}

    # This joint adds the body prefix str if set to all the bodies in the AMBF
    def add_body_prefix_str(self, urdf_body_str):
        return self.body_name_prefix + urdf_body_str

    # This method add the joint prefix if set to all the joints in AMBF
    def add_joint_prefix_str(self, urdf_joint_str):
        return self.joint_name_prefix + urdf_joint_str

    # Courtesy of:
    # https://blender.stackexchange.com/questions/62040/get-center-of-geometry-of-an-object
    def compute_local_com(self, obj):
        vcos = [ v.co for v in obj.data.vertices ]
        find_center = lambda l: ( max(l) + min(l)) / 2
        x, y, z = [[v[i] for v in vcos] for i in range(3)]
        center = [find_center(axis) for axis in [x, y, z]]
        for i in range(0, 3):
            center[i] = center[i] * obj.scale[i]
        return center

    def generate_body_data(self, ambf_yaml, obj_handle):
        if obj_handle.hide is True:
            return
        body = BodyTemplate()
        body_data = body._ambf_data

        if not compare_body_namespace_with_global(obj_handle.name):
            if get_body_namespace(obj_handle.name) != '':
                body_data['namespace'] = get_body_namespace(obj_handle.name)

        obj_handle_name = remove_namespace_prefix(obj_handle.name)

        body_yaml_name = self.add_body_prefix_str(obj_handle_name)
        output_mesh = bpy.context.scene['mesh_output_type']
        body_data['name'] = obj_handle_name
        # If the obj is root body of a Multi-Body and has children
        # then we should enable the publishing of its joint names
        # and joint positions
        if obj_handle.parent is None and obj_handle.children:
            body_data['publish joint names'] = True
            body_data['publish joint positions'] = True

        world_pos = obj_handle.matrix_world.translation
        world_rot = obj_handle.matrix_world.to_euler()
        body_pos = body_data['location']['position']
        body_rot = body_data['location']['orientation']
        body_pos['x'] = round(world_pos.x, 3)
        body_pos['y'] = round(world_pos.y, 3)
        body_pos['z'] = round(world_pos.z, 3)
        body_rot['r'] = round(world_rot[0], 3)
        body_rot['p'] = round(world_rot[1], 3)
        body_rot['y'] = round(world_rot[2], 3)
        if obj_handle.type == 'EMPTY':
            # Check for a special case for defining joints for parallel linkages
            _is_detached_joint = False
            for _detached_prefix_search_str in CommonConfig.detached_joint_prefix:
                if obj_handle_name.rfind(_detached_prefix_search_str) == 0:
                    _is_detached_joint = True
                    break

            if _is_detached_joint:
                print('INFO: JOINT %s FOR PARALLEL LINKAGE FOUND' % obj_handle_name)
                return
            else:
                body_data['mesh'] = ''

                if obj_handle_name in ['world', 'World', 'WORLD']:
                    body_data['mass'] = 0

                else:
                    body_data['mass'] = 0.1
                    body_data['inertia'] = {'ix': 0.01, 'iy': 0.01, 'iz': 0.01}

        elif obj_handle.type == 'MESH':

            if obj_handle.rigid_body:
                if obj_handle.rigid_body.type == 'PASSIVE':
                    body_data['mass'] = 0.0
                else:
                    body_data['mass'] = round(obj_handle.rigid_body.mass, 3)
                body_data['friction'] = {'rolling': 0.01, 'static': 0.5}
                body_data['damping'] = {'linear': 0.1, 'angular': 0.1}
                body_data['restitution'] = round(obj_handle.rigid_body.restitution)

                body_data['friction']['static'] = round(obj_handle.rigid_body.friction, 3)
                body_data['damping']['linear'] = round(obj_handle.rigid_body.linear_damping, 3)
                body_data['damping']['angular'] = round(obj_handle.rigid_body.angular_damping, 3)

                body_data['collision groups'] = [idx for idx, chk in
                                                 enumerate(obj_handle.rigid_body.collision_groups) if chk == True]

                if obj_handle.rigid_body.use_margin is True:
                    body_data['collision margin'] = round(obj_handle.rigid_body.collision_margin, 3)

                # Now lets load the loaded data if any from loaded AMBF File
                _body_col_geo_already_defined = False
                if obj_handle in CommonConfig.loaded_body_map:
                    if 'collision shape' in CommonConfig.loaded_body_map[obj_handle]:
                        _body_col_geo_already_defined = True

                if obj_handle.rigid_body.collision_shape not in ['CONVEX_HULL', 'MESH']:
                    ocs = obj_handle.rigid_body.collision_shape
                    # There isn't a mechanism to change the collision shapes much in Blender. For this reason
                    # if a collision shape has already been defined in the loaded AMBF and it matches the shape
                    # for the Blender body, just use the shape and geometry from the loaded AMBF Config Body
                    if _body_col_geo_already_defined and ocs == CommonConfig.loaded_body_map[obj_handle]['collision shape']:
                        body_data['collision shape'] = CommonConfig.loaded_body_map[obj_handle]['collision shape']
                        body_data['collision geometry'] = CommonConfig.loaded_body_map[obj_handle]['collision geometry']
                    else:
                        body_data['collision shape'] = ocs
                        bcg = OrderedDict()
                        dims = obj_handle.dimensions.copy()
                        od = [round(dims[0], 3), round(dims[1], 3), round(dims[2], 3)]
                        # Now we need to find out the geometry of the shape
                        if ocs == 'BOX':
                            bcg = {'x': od[0], 'y': od[1], 'z': od[2]}
                        elif ocs == 'SPHERE':
                            bcg = {'radius': max(od)/2.0}
                        elif ocs == 'CYLINDER':
                            major_ax_char, major_ax_idx = get_major_axis(od)
                            median_ax_char, median_ax_idx = get_median_axis(od)
                            bcg = {'radius': od[median_ax_idx]/2.0, 'height': od[major_ax_idx], 'axis': major_ax_char}
                        elif ocs == 'CAPSULE':
                            major_ax_char, major_ax_idx = get_major_axis(od)
                            median_ax_char, median_ax_idx = get_median_axis(od)
                            bcg = {'radius': od[median_ax_idx]/2.0, 'height': od[major_ax_idx], 'axis': major_ax_char}
                        elif ocs == 'CONE':
                            major_ax_char, major_ax_idx = get_major_axis(od)
                            median_ax_char, median_ax_idx = get_median_axis(od)
                            bcg = {'radius': od[median_ax_idx]/2.0, 'height': od[major_ax_idx], 'axis': major_ax_char}

                        body_data['collision geometry'] = bcg

            del body_data['inertia']
            body_data['mesh'] = obj_handle_name + get_extension(output_mesh)
            body_com = self.compute_local_com(obj_handle)
            body_d_pos = body_data['inertial offset']['position']
            body_d_pos['x'] = round(body_com[0], 3)
            body_d_pos['y'] = round(body_com[1], 3)
            body_d_pos['z'] = round(body_com[2], 3)

            if obj_handle.data.materials:
                del body_data['color']
                body_data['color components'] = OrderedDict()
                body_data['color components'] = {'diffuse': {'r': 1.0, 'g': 1.0, 'b': 1.0},
                                                 'specular': {'r': 1.0, 'g': 1.0, 'b': 1.0},
                                                 'ambient': {'level': 0.5},
                                                 'transparency': 1.0}

                body_data['color components']['diffuse']['r'] = round(obj_handle.data.materials[0].diffuse_color[0], 4)
                body_data['color components']['diffuse']['g'] = round(obj_handle.data.materials[0].diffuse_color[1], 4)
                body_data['color components']['diffuse']['b'] = round(obj_handle.data.materials[0].diffuse_color[2], 4)

                body_data['color components']['specular']['r'] = round(obj_handle.data.materials[0].specular_color[0], 4)
                body_data['color components']['specular']['g'] = round(obj_handle.data.materials[0].specular_color[1], 4)
                body_data['color components']['specular']['b'] = round(obj_handle.data.materials[0].specular_color[2], 4)

                body_data['color components']['ambient']['level'] = round(obj_handle.data.materials[0].ambient, 4)

                body_data['color components']['transparency'] = round(obj_handle.data.materials[0].alpha, 4)
            
            # Set the body controller data from the controller props
            if obj_handle.ambf_enable_body_props is True:
                _controller_gains = OrderedDict()
                _lin_gains = OrderedDict()
                _ang_gains = OrderedDict()
                _lin_gains['P'] = round(obj_handle.ambf_linear_controller_p_gain, 4)
                _lin_gains['I'] = 0
                _lin_gains['D'] = round(obj_handle.ambf_linear_controller_d_gain, 4)
                _ang_gains['P'] = round(obj_handle.ambf_angular_controller_p_gain, 4)
                _ang_gains['I'] = 0
                _ang_gains['D'] = round(obj_handle.ambf_angular_controller_d_gain, 4)
                _controller_gains['linear'] = _lin_gains
                _controller_gains['angular'] = _ang_gains
                body_data['controller'] = _controller_gains
            else:
                if 'controller' in body_data:
                    del body_data['controller']

        ambf_yaml[body_yaml_name] = body_data
        self._body_names_list.append(body_yaml_name)

    def generate_joint_data(self, ambf_yaml, obj_handle):

        if obj_handle.hide is True:
            return

        if obj_handle.rigid_body_constraint:
            if obj_handle.rigid_body_constraint.object1:
                if obj_handle.rigid_body_constraint.object1.hide is True:
                    return
            if obj_handle.rigid_body_constraint.object2:
                if obj_handle.rigid_body_constraint.object2.hide is True:
                    return

            if obj_handle.rigid_body_constraint.type in ['FIXED', 'HINGE', 'SLIDER', 'POINT', 'GENERIC', 'GENERIC_SPRING']:
                constraint = obj_handle.rigid_body_constraint
                joint_template = JointTemplate()
                joint_data = joint_template._ambf_data
                if constraint.object1:
                    parent_obj_handle = constraint.object1
                    child_obj_handle = constraint.object2

                    parent_obj_handle_name = remove_namespace_prefix(parent_obj_handle.name)
                    child_obj_handle_name = remove_namespace_prefix(child_obj_handle.name)
                    obj_handle_name = remove_namespace_prefix(obj_handle.name)

                    joint_data['name'] = parent_obj_handle_name + "-" + child_obj_handle_name
                    joint_data['parent'] = self.add_body_prefix_str(parent_obj_handle_name)
                    joint_data['child'] = self.add_body_prefix_str(child_obj_handle_name)
                    # parent_body_data = self._ambf_yaml[self.get_body_prefixed_name(parent_obj_handle_name)]
                    # child_body_data = self._ambf_yaml[self.get_body_prefixed_name(child_obj_handle_name)]

                    # Since there isn't a convenient way of defining detached joints and hence parallel linkages due
                    # to the limit on 1 parent per body. We use a hack. This is how it works. In Blender
                    # we start by defining an empty body that has to have specific prefix as the first word
                    # in its name. Next we set up the rigid_body_constraint of this empty body as follows. For the RBC
                    # , we set the parent of this empty body to be the link that we want as the parent for
                    # the detached joint and the child body as the corresponding child of the detached joint.
                    # Remember, this empty body is only a place holder and won't be added to
                    # AMBF. Next, its recommended to set the parent body of this empty body as it's parent in Blender
                    # and by that I mean the tree hierarchy parent (having set the the parent in the rigid body
                    # constraint property is different from parent in tree hierarchy). From there on, make sure to
                    # rotate this empty body such that the z axis / x axis is in the direction of the joint axis if
                    # the detached joint is supposed to be revolute or prismatic respectively.
                    _is_detached_joint = False
                    if obj_handle.type == 'EMPTY':
                        # Check for a special case for defining joints for parallel linkages
                        for _detached_prefix_search_str in CommonConfig.detached_joint_prefix:
                            if obj_handle_name.rfind(_detached_prefix_search_str) == 0:
                                _is_detached_joint = True
                                break

                    if _is_detached_joint:
                        print('INFO: FOR BODY \"%s\" ADDING DETACHED JOINT' % obj_handle_name)

                        parent_pivot, parent_axis = self.compute_pivot_and_axis(
                            parent_obj_handle, obj_handle, constraint)

                        child_pivot, child_axis = self.compute_pivot_and_axis(
                            child_obj_handle, obj_handle, constraint)
                        # Add this field to the joint data, it will come in handy for blender later
                        joint_data['detached'] = True

                    else:
                        parent_pivot, parent_axis = self.compute_pivot_and_axis(
                            parent_obj_handle, child_obj_handle, constraint)
                        child_pivot = mathutils.Vector([0, 0, 0])
                        child_axis, ax_idx = self.get_joint_axis(constraint)

                    parent_pivot_data = joint_data["parent pivot"]
                    parent_axis_data = joint_data["parent axis"]
                    parent_pivot_data['x'] = round(parent_pivot.x, 3)
                    parent_pivot_data['y'] = round(parent_pivot.y, 3)
                    parent_pivot_data['z'] = round(parent_pivot.z, 3)
                    parent_axis_data['x'] = round(parent_axis.x, 3)
                    parent_axis_data['y'] = round(parent_axis.y, 3)
                    parent_axis_data['z'] = round(parent_axis.z, 3)
                    child_pivot_data = joint_data["child pivot"]
                    child_axis_data = joint_data["child axis"]
                    child_pivot_data['x'] = round(child_pivot.x, 3)
                    child_pivot_data['y'] = round(child_pivot.y, 3)
                    child_pivot_data['z'] = round(child_pivot.z, 3)
                    child_axis_data['x'] = round(child_axis.x, 3)
                    child_axis_data['y'] = round(child_axis.y, 3)
                    child_axis_data['z'] = round(child_axis.z, 3)

                    # This method assigns joint limits, joint_type, joint damping and stiffness for spring joints
                    self.assign_joint_params(constraint, joint_data)

                    # The use of pivot and axis does not fully define the connection and relative
                    # transform between two bodies it is very likely that we need an additional offset
                    # of the child body as in most of the cases of URDF's For this purpose, we calculate
                    # the offset as follows
                    r_c_p_ambf = rot_matrix_from_vecs(child_axis, parent_axis)
                    r_p_c_ambf = r_c_p_ambf.to_3x3().copy()
                    r_p_c_ambf.invert()

                    t_p_w = parent_obj_handle.matrix_world.copy()
                    r_w_p = t_p_w.to_3x3().copy()
                    r_w_p.invert()
                    r_c_w = child_obj_handle.matrix_world.to_3x3().copy()
                    r_c_p_blender = r_w_p * r_c_w

                    r_angular_offset = r_p_c_ambf * r_c_p_blender

                    offset_axis_angle = r_angular_offset.to_quaternion().to_axis_angle()

                    if abs(offset_axis_angle[1]) > 0.01:
                        # print '*****************************'
                        # print joint_data['name']
                        # print 'Joint Axis, '
                        # print '\t', joint.axis
                        # print 'Offset Axis'
                        # print '\t', offset_axis_angle[1]
                        offset_angle = round(offset_axis_angle[1], 3)
                        # offset_angle = round(offset_angle, 3)
                        # print 'Offset Angle: \t', offset_angle
                        # print('OFFSET ANGLE', offset_axis_angle[1])
                        # print('CHILD AXIS', child_axis)
                        # print('OFFSET AXIS', offset_axis_angle)
                        # print('DOT PRODUCT', parent_axis.dot(offset_axis_angle[0]))

                        if abs(1.0 - child_axis.dot(offset_axis_angle[0])) < 0.1:
                            joint_data['offset'] = offset_angle
                            # print ': SAME DIRECTION'
                        elif abs(1.0 + child_axis.dot(offset_axis_angle[0])) < 0.1:
                            joint_data['offset'] = -offset_angle
                            # print ': OPPOSITE DIRECTION'
                        else:
                            print('ERROR: SHOULD\'NT GET HERE')

                    joint_yaml_name = self.add_joint_prefix_str(joint_data['name'])
                    ambf_yaml[joint_yaml_name] = joint_data
                    self._joint_names_list.append(joint_yaml_name)

                    # Finally get some rough values for the controller gains
                    p_mass = 0.01
                    c_mass = 0.01
                    if parent_obj_handle.rigid_body:
                        p_mass = parent_obj_handle.rigid_body.mass
                    if child_obj_handle.rigid_body:
                        c_mass = child_obj_handle.rigid_body.mass

                    # Set the joint controller gains data from the joint controller props
                    if obj_handle.rigid_body_constraint.type in ['HINGE', 'SLIDER', 'GENERIC']:
                        if obj_handle.ambf_enable_joint_props is True:
                            _gains = OrderedDict()
                            _gains['P'] = round(obj_handle.ambf_joint_controller_p_gain, 4)
                            _gains['I'] = 0
                            _gains['D'] = round(obj_handle.ambf_joint_controller_d_gain, 4)
                            
                            joint_data['controller'] = _gains
                            joint_data['damping'] = round(obj_handle.ambf_joint_damping, 4)
                        else:
                            if 'controller' in joint_data:
                                del joint_data['controller']

    # Get the joints axis as a vector
    def get_joint_axis(self, constraint):
        if constraint.type == 'HINGE':
            ax_idx = 2
        elif constraint.type == 'SLIDER':
            ax_idx = 0
        elif constraint.type == 'GENERIC':
            ax_idx = 2
            if constraint.use_limit_lin_x or constraint.use_limit_ang_x:
                ax_idx = 0
            elif constraint.use_limit_lin_y or constraint.use_limit_ang_y:
                ax_idx = 1
            elif constraint.use_limit_lin_z or constraint.use_limit_ang_z:
                ax_idx = 2
        elif constraint.type == 'GENERIC_SPRING':
            if constraint.use_limit_lin_x or constraint.use_limit_ang_x:
                ax_idx = 0
            elif constraint.use_limit_lin_y or constraint.use_limit_ang_y:
                ax_idx = 1
            elif constraint.use_limit_lin_z or constraint.use_limit_ang_z:
                ax_idx = 2
        elif constraint.type == 'POINT':
            ax_idx = 2
        elif constraint.type == 'FIXED':
            ax_idx = 2
        # The third col of rotation matrix is the z axis of child in parent
        joint_axis = mathutils.Vector([0, 0, 0])
        joint_axis[ax_idx] = 1.0
        return joint_axis, ax_idx

    # Since changing the scale of the bodies directly impacts the rotation matrix, we have
    # to take that into account while calculating offset of child from parent using
    # transform manipulation
    def compute_pivot_and_axis(self, parent, child, constraint):
        # Since the rotation matrix is carrying the scale, separate out just
        # the rotation component
        # Transform of Parent in World
        t_p_w = parent.matrix_world.copy().to_euler().to_matrix().to_4x4()
        t_p_w.translation = parent.matrix_world.copy().translation

        # Since the rotation matrix is carrying the scale, separate out just
        # the rotation component
        # Transform of Child in World
        t_c_w = child.matrix_world.copy().to_euler().to_matrix().to_4x4()
        t_c_w.translation = child.matrix_world.copy().translation

        # Copy over the transform to invert it
        t_w_p = t_p_w.copy()
        t_w_p.invert()
        # Transform of Child in Parent
        # t_c_p = t_w_p * t_c_w
        t_c_p = t_w_p * t_c_w
        pivot = t_c_p.translation

        joint_axis, axis_idx = self.get_joint_axis(constraint)
        # The third col of rotation matrix is the z axis of child in parent
        axis = mathutils.Vector(t_c_p.col[axis_idx][0:3])
        return pivot, axis

    # Assign the joint parameters that include joint limits, type, damping and joint stiffness for spring joints
    def assign_joint_params(self, constraint, joint_data):
        if constraint.type == 'HINGE':
            if constraint.use_limit_ang_z:
                joint_data['type'] = 'revolute'
                higher_limit = constraint.limit_ang_z_upper
                lower_limit = constraint.limit_ang_z_lower
            else:
                joint_data['type'] = 'continuous'

        elif constraint.type == 'SLIDER':
            joint_data['type'] = 'prismatic'
            higher_limit = constraint.limit_lin_x_upper
            lower_limit = constraint.limit_lin_x_lower

        elif constraint.type == 'GENERIC':

            if constraint.use_limit_lin_x:
                joint_data['type'] = 'prismatic'
                higher_limit = constraint.limit_lin_x_upper
                lower_limit = constraint.limit_lin_x_lower

            elif constraint.use_limit_lin_y:
                joint_data['type'] = 'prismatic'
                higher_limit = constraint.limit_lin_y_upper
                lower_limit = constraint.limit_lin_y_lower

            elif constraint.use_limit_lin_z:
                joint_data['type'] = 'prismatic'
                higher_limit = constraint.limit_lin_z_upper
                lower_limit = constraint.limit_lin_z_lower

            elif constraint.use_limit_ang_x:
                joint_data['type'] = 'revolute'
                higher_limit = constraint.limit_ang_x_upper
                lower_limit = constraint.limit_ang_x_lower

            elif constraint.use_limit_ang_y:
                joint_data['type'] = 'revolute'
                higher_limit = constraint.limit_ang_y_upper
                lower_limit = constraint.limit_ang_y_lower

            elif constraint.use_limit_ang_z:
                joint_data['type'] = 'revolute'
                higher_limit = constraint.limit_ang_z_upper
                lower_limit = constraint.limit_ang_z_lower

        elif constraint.type == 'GENERIC_SPRING':

            if constraint.use_limit_lin_x:
                joint_data['type'] = 'linear spring'
                higher_limit = constraint.limit_lin_x_upper
                lower_limit = constraint.limit_lin_x_lower
                if constraint.use_spring_x:
                    joint_data['damping'] = constraint.spring_damping_x
                    joint_data['stiffness'] = constraint.spring_stiffness_x

            elif constraint.use_limit_lin_y:
                joint_data['type'] = 'linear spring'
                higher_limit = constraint.limit_lin_y_upper
                lower_limit = constraint.limit_lin_y_lower
                if constraint.use_spring_y:
                    joint_data['damping'] = constraint.spring_damping_y
                    joint_data['stiffness'] = constraint.spring_stiffness_y

            elif constraint.use_limit_lin_z:
                joint_data['type'] = 'linear spring'
                higher_limit = constraint.limit_lin_z_upper
                lower_limit = constraint.limit_lin_z_lower
                if constraint.use_spring_z:
                    joint_data['damping'] = constraint.spring_damping_z
                    joint_data['stiffness'] = constraint.spring_stiffness_z

            elif constraint.use_limit_ang_x:
                joint_data['type'] = 'torsion spring'
                higher_limit = constraint.limit_ang_x_upper
                lower_limit = constraint.limit_ang_x_lower
                if constraint.use_spring_ang_x:
                    joint_data['damping'] = constraint.spring_damping_ang_x
                    joint_data['stiffness'] = constraint.spring_stiffness_ang_x

            elif constraint.use_limit_ang_y:
                joint_data['type'] = 'torsion spring'
                higher_limit = constraint.limit_ang_y_upper
                lower_limit = constraint.limit_ang_y_lower
                if constraint.use_spring_ang_y:
                    joint_data['damping'] = constraint.spring_damping_ang_y
                    joint_data['stiffness'] = constraint.spring_stiffness_ang_y

            elif constraint.use_limit_ang_z:
                joint_data['type'] = 'torsion spring'
                higher_limit = constraint.limit_ang_z_upper
                lower_limit = constraint.limit_ang_z_lower
                if constraint.use_spring_ang_z:
                    joint_data['damping'] = constraint.spring_damping_ang_z
                    joint_data['stiffness'] = constraint.spring_stiffness_ang_z

        elif constraint.type == 'POINT':
            joint_data['type'] = 'p2p'

        elif constraint.type == 'FIXED':
            joint_data['type'] = 'fixed'

        if joint_data['type'] in ['fixed', 'p2p']:
            del joint_data["joint limits"]

        elif joint_data['type'] == 'continuous':
            del joint_data["joint limits"]['low']
            del joint_data["joint limits"]['high']

        else:
            joint_limit_data = joint_data["joint limits"]
            joint_limit_data['low'] = round(lower_limit, 3)
            joint_limit_data['high'] = round(higher_limit, 3)

    def generate_ambf_yaml(self):
        num_objs = len(bpy.data.objects)
        save_to = bpy.path.abspath(self._context.scene.ambf_yaml_conf_path)
        filename = os.path.basename(save_to)
        save_dir = os.path.dirname(save_to)
        if not filename:
            filename = 'default.yaml'
        output_filename = os.path.join(save_dir, filename)
        # if a file exists by that name, save a backup
        if os.path.isfile(output_filename):
            os.rename(output_filename, output_filename + '.old')
        output_file = open(output_filename, 'w')
        print('Output filename is: ', output_filename)

        # For inorder processing, set the bodies and joints tag at the top of the map
        self._ambf_yaml = OrderedDict()
        
        self._ambf_yaml['bodies'] = []
        self._ambf_yaml['joints'] = []
        print('SAVE PATH', bpy.path.abspath(save_dir))
        print('AMBF CONFIG PATH', bpy.path.abspath(self._context.scene.ambf_yaml_mesh_path))
        rel_mesh_path = os.path.relpath(bpy.path.abspath(self._context.scene.ambf_yaml_mesh_path), bpy.path.abspath(save_dir))

        self._ambf_yaml['high resolution path'] = rel_mesh_path + '/high_res/'
        self._ambf_yaml['low resolution path'] = rel_mesh_path + '/low_res/'

        self._ambf_yaml['ignore inter-collision'] = self._context.scene.ignore_inter_collision

        update_global_namespace(self._context)

        if CommonConfig.namespace is not "":
            self._ambf_yaml['namespace'] = CommonConfig.namespace

        # We want in-order processing, so make sure to
        # add bodies to ambf in a hierarchial fashion.

        _heirarichal_bodies_list = populate_heirarchial_tree()

        for body in _heirarichal_bodies_list:
            self.generate_body_data(self._ambf_yaml, body)

        for body in _heirarichal_bodies_list:
            self.generate_joint_data(self._ambf_yaml, body)

        # Now populate the bodies and joints tag
        self._ambf_yaml['bodies'] = self._body_names_list
        self._ambf_yaml['joints'] = self._joint_names_list
        
        yaml.dump(self._ambf_yaml, output_file)

        header_str = "# AMBF Version: %s\n" \
                     "# Generated By: ambf_addon for Blender %s\n" \
                     "# Link: %s\n" \
                     "# Generated on: %s\n"\
                     % (str(bl_info['version']).replace(', ', '.'),
                        str(bl_info['blender']).replace(', ', '.'),
                        bl_info['wiki_url'],
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        prepend_comment_to_file(output_filename, header_str)


class AMBF_OT_save_meshes(bpy.types.Operator):
    bl_idname = "ambf.save_meshes"
    bl_label = "Save Meshes"
    bl_description = "This saves the meshes in base folder specifed in the field above. Two folders" \
                     " are created in the base folder named, \"high_res\" and \"low_res\" to store the" \
                     " high-res and low-res meshes separately"

    def execute(self, context):
        replace_dot_from_obj_names()
        self.save_meshes(context)
        return {'FINISHED'}

    # This recursive function is specialized to deal with
    # tree based hierarchy. In such cases we have to ensure
    # to move the parent to origin first and then its children successively
    # otherwise moving any parent after its child has been moved with move the
    # child as well
    def set_to_origin(self, p_obj, obj_name_mat_list):
        if p_obj.children is None:
            return
        obj_name_mat_list.append([p_obj.name, p_obj.matrix_world.copy()])
        # Since setting the world transform clears the embedded scale
        # of the object, we need to re-scale the object after putting it to origin
        scale_mat = mathutils.Matrix()
        scale_mat = scale_mat.Scale(p_obj.matrix_world.median_scale, 4)
        p_obj.matrix_world.identity()
        p_obj.matrix_world = scale_mat
        for c_obj in p_obj.children:
            self.set_to_origin(c_obj, obj_name_mat_list)

    # Since Blender exports meshes w.r.t world transform and not the
    # the local mesh transform, we explicitly push each object to origin
    # and remember its world transform for putting it back later on
    def set_all_meshes_to_origin(self):
        obj_name_mat_list = []
        for p_obj in bpy.data.objects:
            if p_obj.parent is None:
                self.set_to_origin(p_obj, obj_name_mat_list)
        return obj_name_mat_list

    # This recursive function works in similar fashion to the
    # set_to_origin function, but uses the know default transform
    # to set the tree back to default in a hierarchial fashion
    def reset_back_to_default(self, p_obj_handle, obj_name_mat_list):
        if p_obj_handle.children is None:
            return
        for item in obj_name_mat_list:
            if p_obj_handle.name == item[0]:
                p_obj_handle.matrix_world = item[1]
        for c_obj_handle in p_obj_handle.children:
            self.reset_back_to_default(c_obj_handle, obj_name_mat_list)

    def reset_meshes_to_original_position(self, obj_name_mat_list):
        for p_obj_handle in bpy.data.objects:
            if p_obj_handle.parent is None:
                self.reset_back_to_default(p_obj_handle, obj_name_mat_list)

    def save_meshes(self, context):
        # First deselect all objects
        select_all_objects(False)

        save_path = bpy.path.abspath(context.scene.ambf_yaml_mesh_path)
        high_res_path = os.path.join(save_path, 'high_res/')
        low_res_path = os.path.join(save_path, 'low_res/')
        os.makedirs(high_res_path, exist_ok=True)
        os.makedirs(low_res_path, exist_ok=True)
        mesh_type = bpy.context.scene['mesh_output_type']

        mesh_name_mat_list = self.set_all_meshes_to_origin()
        for obj_handle in bpy.data.objects:
            # Mesh Type is .stl
            obj_handle.select = True

            obj_handle_name = remove_namespace_prefix(obj_handle.name)

            if obj_handle.type == 'MESH':
                # First save the texture(s) if any
                # Store current render settings
                _settings = context.scene.render.image_settings

                # Change render settings to our target format
                _settings.file_format = 'PNG'

                for mat in obj_handle.data.materials:
                    for tex_slot in mat.texture_slots:
                        if tex_slot:
                            im = tex_slot.texture.image
                            _existing_path = im.filepath_raw
                            _dir = os.path.dirname(_existing_path)
                            _filename = os.path.basename(_existing_path)
                            _filename_wo_ext = _filename.split('.')[0]
                            _save_as = os.path.join(high_res_path, _filename_wo_ext + '.png')
                            im.filepath_raw = _save_as
                            im.save_render(_save_as)

                if mesh_type == MeshType.meshSTL.value:
                    obj_name = obj_handle_name + '.STL'
                    filename_high_res = os.path.join(high_res_path, obj_name)
                    filename_low_res = os.path.join(low_res_path, obj_name)
                    bpy.ops.export_mesh.stl(filepath=filename_high_res, use_selection=True, use_mesh_modifiers=False)
                    bpy.ops.export_mesh.stl(filepath=filename_low_res, use_selection=True, use_mesh_modifiers=True)
                elif mesh_type == MeshType.meshOBJ.value:
                    obj_name = obj_handle_name + '.OBJ'
                    filename_high_res = os.path.join(high_res_path, obj_name)
                    filename_low_res = os.path.join(low_res_path, obj_name)
                    bpy.ops.export_scene.obj(filepath=filename_high_res, axis_up='Z', axis_forward='Y',
                                             use_selection=True, use_mesh_modifiers=False)
                    bpy.ops.export_scene.obj(filepath=filename_low_res, axis_up='Z', axis_forward='Y',
                                             use_selection=True, use_mesh_modifiers=True)
                elif mesh_type == MeshType.mesh3DS.value:
                    obj_name = obj_handle_name + '.3DS'
                    filename_high_res = os.path.join(high_res_path, obj_name)
                    filename_low_res = os.path.join(low_res_path, obj_name)
                    # 3DS doesn't support supressing modifiers, so we explicitly
                    # toggle them to save as high res and low res meshes
                    # STILL BUGGY
                    for mod in obj_handle.modifiers:
                        mod.show_viewport = True
                    bpy.ops.export_scene.autodesk_3ds(filepath=filename_low_res, use_selection=True)
                    for mod in obj_handle.modifiers:
                        mod.show_viewport = True
                    bpy.ops.export_scene.autodesk_3ds(filepath=filename_high_res, use_selection=True)
                elif mesh_type == MeshType.meshPLY.value:
                    # .PLY export has a bug in which it only saves the mesh that is
                    # active in context of view. Hence we explicitly select this object
                    # as active in the scene on top of being selected
                    obj_name = obj_handle_name + '.PLY'
                    filename_high_res = os.path.join(high_res_path, obj_name)
                    filename_low_res = os.path.join(low_res_path, obj_name)
                    bpy.context.scene.objects.active = obj_handle
                    bpy.ops.export_mesh.ply(filepath=filename_high_res, use_mesh_modifiers=False)
                    bpy.ops.export_mesh.ply(filepath=filename_low_res, use_mesh_modifiers=True)
                    # Make sure to deselect the mesh
                    bpy.context.scene.objects.active = None

            obj_handle.select = False
        self.reset_meshes_to_original_position(mesh_name_mat_list)


class AMBF_OT_generate_low_res_mesh_modifiers(bpy.types.Operator):
    bl_idname = "ambf.generate_low_res_mesh_modifiers"
    bl_label = "Generate Low-Res Meshes"
    bl_description = "This creates the low-res modifiers for higher speed collision computation" \
                     " . For now, the mesh decimation modifiers are being used but they shall be" \
                     " replaced with other methods"

    def execute(self, context):
        # First off, remove any existing Modifiers:
        bpy.ops.ambf.remove_low_res_mesh_modifiers()

        # Now deselect all objects
        for obj in bpy.data.objects:
            obj.select = False

        vertices_max = context.scene.mesh_max_vertices
        # Select each object iteratively and generate its low-res mesh
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and obj.hide is False:
                decimate_mod = obj.modifiers.new('decimate_mod', 'DECIMATE')
                if len(obj.data.vertices) > vertices_max:
                    reduction_ratio = vertices_max / len(obj.data.vertices)
                    decimate_mod.use_symmetry = False
                    decimate_mod.use_collapse_triangulate = True
                    decimate_mod.ratio = reduction_ratio
                    decimate_mod.show_viewport = True
        return {'FINISHED'}


class AMBF_OT_create_detached_joint(bpy.types.Operator):
    bl_idname = "ambf.create_detached_joint"
    bl_label = "Create Detached Joint"
    bl_description = "This creates an empty object that can be used to create closed loop mechanisms. Make" \
                     " sure to set the rigid body constraint (RBC) for this empty mesh and ideally parent this empty" \
                     " object with the parent body of its RBC"

    def execute(self, context):
        select_all_objects(False)
        bpy.ops.object.empty_add(type='PLAIN_AXES')
        context.active_object.name = CommonConfig.detached_joint_prefix[0] + ' joint'
        return {'FINISHED'}


class AMBF_OT_remove_low_res_mesh_modifiers(bpy.types.Operator):
    bl_idname = "ambf.remove_low_res_mesh_modifiers"
    bl_label = "Remove All Modifiers"
    bl_description = "This removes all the mesh modifiers generated for meshes in the current scene"

    def execute(self, context):
        for obj in bpy.data.objects:
            for mod in obj.modifiers:
                obj.modifiers.remove(mod)
        return {'FINISHED'}


class AMBF_OT_toggle_low_res_mesh_modifiers_visibility(bpy.types.Operator):
    bl_idname = "ambf.toggle_low_res_mesh_modifiers_visibility"
    bl_label = "Toggle Modifiers Visibility"
    bl_description = "This hides all the mesh modifiers generated for meshes in the current scene"

    def execute(self, context):
        for obj in bpy.data.objects:
            for mod in obj.modifiers:
                mod.show_viewport = not mod.show_viewport
        return {'FINISHED'}


class AMBF_OT_remove_object_namespaces(bpy.types.Operator):
    bl_idname = "ambf.remove_object_namespaces"
    bl_label = "Remove Object Namespaces"
    bl_description = "This removes any current object namespaces"

    def execute(self, context):
        for obj in bpy.data.objects:
            obj.name = obj.name.split('/')[-1]
        return {'FINISHED'}


class AMBF_OT_load_ambf_file(bpy.types.Operator):
    bl_idname = "ambf.load_ambf_file"
    bl_label = "Load AMBF Description File (ADF)"
    bl_description = "This loads an AMBF from the specified config file"

    def __init__(self):
        self._ambf_data = None
        # A dict of T_c_j frames for each body
        self._body_T_j_c = {}
        self._joint_additional_offset = {}
        # A dict for body name as defined in YAML File and the Name Blender gives
        # the body
        self._blender_remapped_body_names = {}
        self._high_res_path = ''
        self._low_res_path = ''
        self._context = None
        self._yaml_filepath = ''

    def get_qualified_path(self, path):
        filepath = Path(path)

        if filepath.is_absolute():
            return path
        else:
            ambf_filepath = Path(self._yaml_filepath)
            path = str(ambf_filepath.parent.joinpath(filepath))
            return path

    def load_mesh(self, body_data, body_name):

        af_name = body_data['name']

        if 'high resolution path' in body_data:
            body_high_res_path = self.get_qualified_path(body_data['high resolution path'])
        else:
            body_high_res_path = self._high_res_path
        # If body name is world. Check if a world body has already
        # been defined, and if it has been, ignore adding another world body
        if af_name in ['world', 'World', 'WORLD']:
            for temp_obj_handle in bpy.data.objects:
                if temp_obj_handle.type in ['MESH', 'EMPTY']:
                    if temp_obj_handle.name in ['world', 'World', 'WORLD']:
                        self._blender_remapped_body_names[body_name] = temp_obj_handle.name
                        self._body_T_j_c[body_name] = mathutils.Matrix()
                        return
        body_mesh_name = body_data['mesh']

        mesh_filepath = Path(os.path.join(body_high_res_path, body_mesh_name))

        if mesh_filepath.suffix in ['.stl', '.STL']:
            bpy.ops.import_mesh.stl(filepath=str(mesh_filepath.resolve()))

        elif mesh_filepath.suffix in ['.obj', '.OBJ']:
            _manually_select_obj = True
            bpy.ops.import_scene.obj(filepath=str(mesh_filepath.resolve()), axis_up='Z', axis_forward='Y')
            # Hack, .3ds and .obj imports do not make the imported object active. A hack is
            # to capture the selected objects in this case.
            self._context.scene.objects.active = self._context.selected_objects[0]

        elif mesh_filepath.suffix in ['.dae', '.DAE']:
            bpy.ops.wm.collada_import(filepath=str(mesh_filepath.resolve()))
            # If we are importing .dae meshes, they can import stuff other than meshes, such as cameras etc.
            # We should remove these extra things and only keep the meshes
            for temp_obj_handle in self._context.selected_objects:
                if temp_obj_handle.type == 'MESH':
                    obj_handle = temp_obj_handle
                    # self._context.scene.objects.active = obj_handle
                else:
                    bpy.data.objects.remove(temp_obj_handle)

            so = bpy.context.selected_objects
            if len(so) > 1:
                self._context.scene.objects.active = so[0]
                bpy.ops.object.join()
                self._context.active_object.name = af_name
                obj_handle = self._context.active_object

                # The lines below are essential in joint the multiple meshes
                # defined in the .dae into one mesh, secondly, making sure that
                # the origin of the mesh is what it is supposed to be as
                # using the join() function call alters the mesh origin
                trans_o = obj_handle.matrix_world.copy()
                obj_handle.matrix_world.identity()
                obj_handle.data.transform(trans_o)

                # Kind of a hack, blender is spawning the collada file
                # a 90 deg offset along the axis axis, this is to correct that
                # Maybe this will not be needed in future versions of blender
                r_x = mathutils.Matrix.Rotation(-1.57, 4, 'X')
                obj_handle.data.transform(r_x)
            else:
                self._context.scene.objects.active = so[0]

        elif mesh_filepath.suffix in ['.3ds', '.3DS']:
            _manually_select_obj = True
            bpy.ops.import_scene.autodesk_3ds(filepath=str(mesh_filepath.resolve()))
            # Hack, .3ds and .obj imports do not make the imported object active. A hack is
            # to capture the selected objects in this case.
            self._context.scene.objects.active = self._context.selected_objects[0]

        elif mesh_filepath.suffix == '':
            bpy.ops.object.empty_add(type='PLAIN_AXES')

        return self._context.active_object

    def load_material(self, body_data, obj_handle):

        af_name = body_data['name']

        if 'color rgba' in body_data:
            mat = bpy.data.materials.new(name=af_name + 'mat')
            mat.diffuse_color[0] = body_data['color rgba']['r']
            mat.diffuse_color[1] = body_data['color rgba']['g']
            mat.diffuse_color[2] = body_data['color rgba']['b']
            mat.use_transparency = True
            mat.transparency_method = 'Z_TRANSPARENCY'
            mat.alpha = body_data['color rgba']['a']
            obj_handle.data.materials.append(mat)

        elif 'color components' in body_data:
            mat = bpy.data.materials.new(name=af_name + 'mat')
            mat.diffuse_color[0] = body_data['color components']['diffuse']['r']
            mat.diffuse_color[1] = body_data['color components']['diffuse']['g']
            mat.diffuse_color[2] = body_data['color components']['diffuse']['b']

            mat.specular_color[0] = body_data['color components']['specular']['r']
            mat.specular_color[1] = body_data['color components']['specular']['g']
            mat.specular_color[2] = body_data['color components']['specular']['b']

            mat.ambient = body_data['color components']['ambient']['level']
            mat.use_transparency = True
            mat.transparency_method = 'Z_TRANSPARENCY'
            mat.alpha = body_data['color components']['transparency']
            obj_handle.data.materials.append(mat)

    def load_blender_rigid_body(self, body_data, obj_handle):

        if obj_handle.type == 'MESH':
            bpy.ops.rigidbody.object_add()
            body_mass = body_data['mass']
            if body_mass is 0.0:
                obj_handle.rigid_body.type = 'PASSIVE'
            else:
                obj_handle.rigid_body.mass = body_mass

            # Finally add the rigid body data if defined
            if 'friction' in body_data:
                if 'static' in body_data['friction']:
                    obj_handle.rigid_body.friction = body_data['friction']['static']

            if 'damping' in body_data:
                if 'linear' in body_data['damping']:
                    obj_handle.rigid_body.linear_damping = body_data['damping']['linear']
                if 'angular' in body_data['damping']:
                    obj_handle.rigid_body.angular_damping = body_data['damping']['angular']

            if 'restitution' in body_data:
                obj_handle.rigid_body.restitution = body_data['restitution']

            if 'collision margin' in body_data:
                obj_handle.rigid_body.collision_margin = body_data['collision margin']

            if 'collision shape' in body_data:
                obj_handle.rigid_body.collision_shape = body_data['collision shape']

            if 'collision groups' in body_data:
                col_groups = body_data['collision groups']
                # First clear existing collision group of 0
                obj_handle.rigid_body.collision_groups[0] = False
                for group in col_groups:
                    if 0 <= group < 20:
                        obj_handle.rigid_body.collision_groups[group] = True
                    else:
                        print('WARNING, Collision Group Outside [0-20]')

            # If Body Controller Defined. Set the P and D gains for linera and angular controller prop fields
            if 'controller' in body_data:
                obj_handle.ambf_linear_controller_p_gain = body_data['controller']['linear']['P']
                obj_handle.ambf_linear_controller_d_gain = body_data['controller']['linear']['D']
                obj_handle.ambf_angular_controller_p_gain = body_data['controller']['angular']['P']
                obj_handle.ambf_angular_controller_d_gain = body_data['controller']['angular']['D']
                obj_handle.ambf_enable_body_props = True

    def load_ambf_rigid_body(self, body_data, obj_handle):

        if obj_handle.type == 'MESH':
            obj_handle.ambf_rigid_body_enable = True
            obj_handle.ambf_rigid_body_mass = body_data['mass']

            if body_data['mass'] is 0.0:
                obj_handle.ambf_rigid_body_is_static = True

            if 'inertial offset' in body_data:
                obj_handle.ambf_rigid_body_linear_inertial_offset[0] = body_data['inertial offset']['position']['x']
                obj_handle.ambf_rigid_body_linear_inertial_offset[1] = body_data['inertial offset']['position']['y']
                obj_handle.ambf_rigid_body_linear_inertial_offset[2] = body_data['inertial offset']['position']['z']

                obj_handle.ambf_rigid_body_angular_inertial_offset[0] = body_data['inertial offset']['orientation']['r']
                obj_handle.ambf_rigid_body_angular_inertial_offset[1] = body_data['inertial offset']['orientation']['p']
                obj_handle.ambf_rigid_body_angular_inertial_offset[2] = body_data['inertial offset']['orientation']['y']

            # Finally add the rigid body data if defined
            if 'friction' in body_data:
                if 'static' in body_data['friction']:
                    obj_handle.ambf_rigid_body_friction = body_data['friction']['static']

            if 'damping' in body_data:
                if 'linear' in body_data['damping']:
                    obj_handle.ambf_rigid_body_linear_damping = body_data['damping']['linear']
                if 'angular' in body_data['damping']:
                    obj_handle.ambf_rigid_body_angular_damping = body_data['damping']['angular']

            if 'restitution' in body_data:
                obj_handle.ambf_rigid_body_restitution = body_data['restitution']

            if 'collision margin' in body_data:
                obj_handle.ambf_rigid_body_collision_margin = body_data['collision margin']
                obj_handle.ambf_rigid_body_enable_collision_margin = True

            if 'collision shape' in body_data:
                obj_handle.ambf_rigid_body_collision_shape = body_data['collision shape']

            if 'collision groups' in body_data:
                col_groups = body_data['collision groups']
                # First clear existing collision group of 0
                obj_handle.ambf_rigid_body_collision_groups[0] = False
                for group in col_groups:
                    if 0 <= group < 20:
                        obj_handle.ambf_rigid_body_collision_groups[group] = True
                    else:
                        print('WARNING, Collision Group Outside [0-20]')

            # If Body Controller Defined. Set the P and D gains for linera and angular controller prop fields
            if 'controller' in body_data:
                obj_handle.ambf_rigid_body_linear_controller_p_gain = body_data['controller']['linear']['P']
                obj_handle.ambf_rigid_body_linear_controller_d_gain = body_data['controller']['linear']['D']
                obj_handle.ambf_rigid_body_angular_controller_p_gain = body_data['controller']['angular']['P']
                obj_handle.ambf_rigid_body_angular_controller_d_gain = body_data['controller']['angular']['D']
                obj_handle.ambf_rigid_body_enable_controllers = True

    def load_rigid_body_name(self, body_data, obj_handle):

        af_name = body_data['name']

        if 'namespace' in body_data:
            _body_namespace = body_data['namespace']
            obj_handle.name = _body_namespace + af_name
        else:
            obj_handle.name = add_namespace_prefix(af_name)

    def load_rigid_body_transform(self, body_data, obj_handle):

        bpy.ops.object.transform_apply(scale=True)

        body_location_xyz = {'x': 0, 'y': 0, 'z': 0}
        body_location_rpy = {'r': 0, 'p': 0, 'y': 0}

        if 'location' in body_data:
            if 'position' in body_data['location']:
                body_location_xyz = body_data['location']['position']
            if 'orientation' in body_data['location']:
                body_location_rpy = body_data['location']['orientation']

        obj_handle.matrix_world.translation[0] = body_location_xyz['x']
        obj_handle.matrix_world.translation[1] = body_location_xyz['y']
        obj_handle.matrix_world.translation[2] = body_location_xyz['z']
        obj_handle.rotation_euler = (body_location_rpy['r'],
                                     body_location_rpy['p'],
                                     body_location_rpy['y'])

    def load_body(self, body_name):
        body_data = self._ambf_data[body_name]

        obj_handle = self.load_mesh(body_data, body_name)

        self.load_rigid_body_name(body_data, obj_handle)
        
        if self._context.scene.enable_legacy_loading:
            self.load_blender_rigid_body(body_data, obj_handle)
        else:
            self.load_ambf_rigid_body(body_data, obj_handle)

        self.load_rigid_body_transform(body_data, obj_handle)

        self.load_material(body_data, obj_handle)

        self._blender_remapped_body_names[body_name] = obj_handle.name
        CommonConfig.loaded_body_map[obj_handle] = body_data
        self._body_T_j_c[body_name] = mathutils.Matrix()

    def adjust_body_pivots_and_axis(self):
        for joint_name in self._ambf_data['joints']:
            joint_data = self._ambf_data[joint_name]
            if 'child pivot' in joint_data:

                if self.is_detached_joint(joint_data):
                    print('INFO, JOINT \"%s\" IS DETACHED, NO NEED'
                          ' TO ADJUST CHILD BODY\'S AXIS AND PIVOTS' % joint_name)
                    return

                child_body_name = joint_data['child']
                parent_pivot_data, parent_axis_data = self.get_parent_pivot_and_axis_data(joint_data)
                child_pivot_data, child_axis_data = self.get_child_pivot_and_axis_data(joint_data)

                child_obj_handle = bpy.data.objects[self._blender_remapped_body_names[child_body_name]]

                joint_type = self.get_ambf_joint_type(joint_data)

                standard_pivot_data, standard_axis_data = self.get_standard_pivot_and_axis_data(joint_data)

                # Universal Constraint Axis
                constraint_axis = mathutils.Vector([standard_axis_data['x'],
                                                    standard_axis_data['y'],
                                                    standard_axis_data['z']])

                # Child's Joint Axis in child's frame
                child_axis = mathutils.Vector([child_axis_data['x'],
                                               child_axis_data['y'],
                                               child_axis_data['z']])

                # To keep the joint limits intact, we set the constraint axis as
                # negative if the joint axis is negative
                # if any(child_axis[i] < 0.0 for i in range(0, 3)):
                #     constraint_axis = -constraint_axis

                R_j_c, d_angle = get_rot_mat_from_vecs(constraint_axis, child_axis)

                # Transformation of joint in child frame
                P_j_c = mathutils.Matrix()
                P_j_c.translation = mathutils.Vector(
                    [child_pivot_data['x'], child_pivot_data['y'], child_pivot_data['z']])
                # Now apply the rotation based on the axis deflection from constraint_axis
                T_j_c = R_j_c
                T_j_c.translation = P_j_c.translation
                T_c_j = T_j_c.copy()
                T_c_j.invert()

                child_pivot_data['x'] = standard_pivot_data['x']
                child_pivot_data['y'] = standard_pivot_data['y']
                child_pivot_data['z'] = standard_pivot_data['z']

                child_axis_data['x'] = standard_axis_data['x']
                child_axis_data['y'] = standard_axis_data['y']
                child_axis_data['z'] = standard_axis_data['z']

                if child_obj_handle.type != 'EMPTY':
                    child_obj_handle.data.transform(T_c_j)
                self._body_T_j_c[joint_data['child']] = T_c_j

                # Implementing the Alignment Offset Correction Algorithm (AO)

                # Parent's Joint Axis in parent's frame
                parent_axis = mathutils.Vector([parent_axis_data['x'],
                                                parent_axis_data['y'],
                                                parent_axis_data['z']])

                R_caxis_p, r_cnew_p_angle = get_rot_mat_from_vecs(constraint_axis, parent_axis)
                R_cnew_p = R_caxis_p * T_c_j
                R_c_p, r_c_p_angle = get_rot_mat_from_vecs(child_axis, parent_axis)
                R_p_cnew = R_cnew_p.copy()
                R_p_cnew.invert()
                delta_R = R_p_cnew * R_c_p
                # print('Joint Name: ', joint_name)
                # print('Delta R: ')
                d_axis_angle = delta_R.to_quaternion().to_axis_angle()
                d_axis = round_vec(d_axis_angle[0])
                d_angle = d_axis_angle[1]
                # Sanity Check: The axis angle should be along the the direction of child axis
                # Throw warning if its not
                v_diff = d_axis.cross(child_axis)
                if v_diff.length > 0.1 and abs(d_angle) > 0.1:
                    print('*** WARNING: AXIS ALIGNMENT LOGIC ERROR')
                # print(d_axis, ' : ', d_angle)
                if any(d_axis[i] < 0.0 for i in range(0, 3)):
                    d_angle = - d_angle

                if abs(d_angle) > 0.1:
                    R_ao = mathutils.Matrix().Rotation(d_angle, 4, constraint_axis)
                    child_obj_handle.data.transform(R_ao)
                    self._body_T_j_c[joint_data['child']] = R_ao * T_c_j
                # end of AO algorithm

            # Finally assign joints and set correct positions

    def get_blender_joint_type(self, joint_data):
        joint_type = 'HINGE'
        if 'type' in joint_data:
            if joint_data['type'] in ['hinge', 'revolute', 'continuous']:
                joint_type = 'HINGE'
            elif joint_data['type'] in ['prismatic', 'slider']:
                joint_type = 'SLIDER'
            elif joint_data['type'] in ['spring', 'linear spring', 'angular spring', 'torsional spring',
                                        'torsion spring']:
                joint_type = 'GENERIC_SPRING'
            elif joint_data['type'] in ['p2p', 'point2point']:
                joint_type = 'POINT'
            elif joint_data['type'] in ['fixed', 'FIXED']:
                joint_type = 'FIXED'

        return joint_type

    def get_ambf_joint_type(self, joint_data):
        joint_type = 'FIXED'
        if 'type' in joint_data:
            if joint_data['type'] in ['hinge', 'revolute', 'continuous']:
                joint_type = 'REVOLUTE'
            elif joint_data['type'] in ['prismatic', 'slider']:
                joint_type = 'PRISMATIC'
            elif joint_data['type'] in ['spring', 'linear spring']:
                joint_type = 'LINEAR_SPRING'
            elif joint_data['type'] in ['angular spring', 'torsional spring', 'torsion spring']:
                joint_type = 'TORSION_SPRING'
            elif joint_data['type'] in ['p2p', 'point2point']:
                joint_type = 'P2P'
            elif joint_data['type'] in ['fixed', 'FIXED']:
                joint_type = 'FIXED'

        return joint_type
    
    def set_default_ambf_constraint_axis(self, joint_obj_handle):
        if joint_obj_handle.ambf_object_type == 'CONSTRAINT':
            if joint_obj_handle.ambf_constraint_type in ['REVOLUTE', 'TORSION_SPRING']:
                joint_obj_handle.ambf_constraint_axis = 'Z'
            elif joint_obj_handle.ambf_constraint_type in ['PRISMATIC', 'LINEAR_SPRING']:
                joint_obj_handle.ambf_constraint_axis = 'X'

    def is_detached_joint(self, joint_data):
        _is_detached_joint = False

        if 'redundant' in joint_data:
            if joint_data['redundant'] is True or joint_data['redundant'] == 'True':
                _is_detached_joint = True

        if 'detached' in joint_data:
            if joint_data['detached'] is True or joint_data['detached'] == 'True':
                _is_detached_joint = True

        return _is_detached_joint

    def has_detached_prefix(self, joint_name):
        _has_detached_prefix = False
        for _detached_joint_name_str in CommonConfig.detached_joint_prefix:
            if joint_name.rfind(_detached_joint_name_str) == 0:
                _has_detached_prefix = True

        return _has_detached_prefix

    def get_parent_and_child_object_handles(self, joint_data):
        parent_body_name = joint_data['parent']
        child_body_name = joint_data['child']

        parent_obj_handle = bpy.data.objects[self._blender_remapped_body_names[parent_body_name]]
        child_obj_handle = bpy.data.objects[self._blender_remapped_body_names[child_body_name]]

        return parent_obj_handle, child_obj_handle

    def get_parent_pivot_and_axis_data(self, joint_data):
        parent_pivot_data = {'x': 0, 'y': 0, 'z': 0}
        parent_axis_data = {'x': 0, 'y': 0, 'z': 1}

        if 'parent pivot' in joint_data:
            parent_pivot_data = joint_data['parent pivot']
        if 'parent axis' in joint_data:
            parent_axis_data = joint_data['parent axis']

        return parent_pivot_data, parent_axis_data

    def get_child_pivot_and_axis_data(self, joint_data):
        child_pivot_data = {'x': 0, 'y': 0, 'z': 0}
        child_axis_data = {'x': 0, 'y': 0, 'z': 1}

        if 'child pivot' in joint_data:
            child_pivot_data = joint_data['child pivot']
        if 'child axis' in joint_data:
            child_axis_data = joint_data['child axis']

        return child_pivot_data, child_axis_data

    def get_standard_pivot_and_axis_data(self, joint_data):
        pivot_data = {'x': 0, 'y': 0, 'z': 0}
        axis_data = {'x': 0, 'y': 0, 'z': 1}
        if joint_data['type'] in ['hinge', 'continuous', 'revolute', 'fixed']:
            axis_data = {'x': 0, 'y': 0, 'z': 1}
        elif joint_data['type'] in ['prismatic', 'slider']:
            axis_data = {'x': 1, 'y': 0, 'z': 0}
        elif joint_data['type'] in ['spring', 'linear spring']:
            axis_data = {'x': 1, 'y': 0, 'z': 0}
        elif joint_data['type'] in ['angular spring', 'torsional spring', 'torsion spring']:
            axis_data = {'x': 0, 'y': 0, 'z': 1}
        elif joint_data['type'] in ['p2p', 'point2point']:
            axis_data = {'x': 0, 'y': 0, 'z': 1}

        return pivot_data, axis_data

    def get_joint_offset_angle(self, joint_data):
        # To fully define a child body's connection and pose in a parent body, just the joint pivots
        # and joint axis are not sufficient. We also need the joint offset which correctly defines
        # the initial pose of the child body in the parent body.
        offset_angle = 0.0
        if not self._context.scene.ignore_ambf_joint_offsets:
            if 'offset' in joint_data:
                offset_angle = joint_data['offset']

        return offset_angle

    def get_joint_in_parent_transform(self, parent_obj_handle, joint_data):
        parent_pivot_data, parent_axis_data = self.get_parent_pivot_and_axis_data(joint_data)
        standard_pivot_data, standard_axis_data = self.get_standard_pivot_and_axis_data(joint_data)

        # Transformation matrix representing parent in world frame
        T_p_w = parent_obj_handle.matrix_world.copy()
        # Parent's Joint Axis in parent's frame
        parent_axis = mathutils.Vector([parent_axis_data['x'],
                                        parent_axis_data['y'],
                                        parent_axis_data['z']])
        # Transformation of joint in parent frame
        P_j_p = mathutils.Matrix()
        P_j_p.translation = mathutils.Vector([parent_pivot_data['x'],
                                              parent_pivot_data['y'],
                                              parent_pivot_data['z']])

        joint_axis = mathutils.Vector([standard_axis_data['x'],
                                       standard_axis_data['y'],
                                       standard_axis_data['z']])

        # Rotation matrix representing child frame in parent frame
        R_j_p, r_j_p_angle = get_rot_mat_from_vecs(joint_axis, parent_axis)

        # Offset along constraint axis
        T_c_offset_rot = mathutils.Matrix().Rotation(self.get_joint_offset_angle(joint_data), 4, parent_axis)

        # Axis Alignment Offset resulting from adjusting the child bodies. If the child bodies are not
        # adjusted, this will be an identity matrix

        T_p_w_off = self._body_T_j_c[joint_data['parent']]
        # Transformation of child in parents frame
        T_j_p = T_p_w * T_p_w_off * P_j_p * T_c_offset_rot * R_j_p

        return T_j_p

    def get_child_in_parent_transform(self, parent_obj_handle, joint_data):
        parent_pivot_data, parent_axis_data = self.get_parent_pivot_and_axis_data(joint_data)
        child_pivot_data, child_axis_data = self.get_child_pivot_and_axis_data(joint_data)

        # Transformation matrix representing parent in world frame
        T_p_w = parent_obj_handle.matrix_world.copy()
        # Parent's Joint Axis in parent's frame
        parent_axis = mathutils.Vector([parent_axis_data['x'],
                                        parent_axis_data['y'],
                                        parent_axis_data['z']])
        # Transformation of joint in parent frame
        P_j_p = mathutils.Matrix()
        # P_j_p = P_j_p * r_j_p
        P_j_p.translation = mathutils.Vector([parent_pivot_data['x'],
                                              parent_pivot_data['y'],
                                              parent_pivot_data['z']])
        child_axis = mathutils.Vector([child_axis_data['x'],
                                       child_axis_data['y'],
                                       child_axis_data['z']])
        # Rotation matrix representing child frame in parent frame
        R_c_p, r_c_p_angle = get_rot_mat_from_vecs(child_axis, parent_axis)
        # print ('r_c_p')
        # print(r_c_p)
        # Transformation of joint in child frame
        P_j_c = mathutils.Matrix()
        # p_j_c *= r_j_c
        # If the child bodies have been adjusted. This pivot data will be all zeros
        P_j_c.translation = mathutils.Vector([child_pivot_data['x'],
                                              child_pivot_data['y'],
                                              child_pivot_data['z']])
        # print(p_j_c)
        # Transformation of child in joints frame
        P_c_j = P_j_c.copy()
        P_c_j.invert()
        # Offset along constraint axis
        T_c_offset_rot = mathutils.Matrix().Rotation(self.get_joint_offset_angle(joint_data), 4, parent_axis)

        # Axis Alignment Offset resulting from adjusting the child bodies. If the child bodies are not
        # adjusted, this will be an identity matrix

        T_p_w_off = self._body_T_j_c[joint_data['parent']]
        # Transformation of child in parents frame
        T_c_p = T_p_w * T_p_w_off * P_j_p * T_c_offset_rot * R_c_p * P_c_j

        return T_c_p

    def get_blender_joint_handle(self, joint_data):
        child_body_name = joint_data['child']

        child_obj_handle = bpy.data.objects[self._blender_remapped_body_names[child_body_name]]

        # If the joint is a detached joint, create an empty axis and return that
        # as the child object handle. Otherwise, return the child obj handle
        # as the joint obj handle
        if self.is_detached_joint(joint_data):
            bpy.ops.object.empty_add(type='PLAIN_AXES')
            joint_obj_handle = bpy.context.active_object
            joint_name = str(joint_data['name'])

            if self.has_detached_prefix(joint_name):
                joint_obj_handle.name = joint_name
            else:
                joint_obj_handle.name = 'detached joint ' + joint_name
        else:
            joint_obj_handle = child_obj_handle

        return joint_obj_handle

    def get_ambf_joint_handle(self, joint_data):
        bpy.ops.object.empty_add(type='PLAIN_AXES')
        joint_obj_handle = bpy.context.active_object
        joint_name = str(joint_data['name'])

        joint_obj_handle.name = joint_name
        joint_obj_handle.scale = 0.1 * joint_obj_handle.scale
        bpy.ops.object.transform_apply(scale=True)

        return joint_obj_handle

    def make_obj1_parent_of_obj2(self, obj1, obj2):
        select_all_objects(False)
        if obj2.parent is None:
            obj2.select = True
            obj1.select = True
            self._context.scene.objects.active = obj1
            bpy.ops.object.parent_set(keep_transform=True)

    def create_blender_constraint(self, joint_obj_handle, joint_type, parent_obj_handle, child_obj_handle):
        self._context.scene.objects.active = joint_obj_handle
        joint_obj_handle.select = True
        bpy.ops.rigidbody.constraint_add(type=joint_type)

        joint_obj_handle.rigid_body_constraint.object1 = parent_obj_handle
        joint_obj_handle.rigid_body_constraint.object2 = child_obj_handle

    def create_ambf_constraint(self, joint_obj_handle, joint_type, parent_obj_handle, child_obj_handle):
        self._context.scene.objects.active = joint_obj_handle
        joint_obj_handle.select = True

        joint_obj_handle.ambf_constraint_enable = True
        joint_obj_handle.ambf_object_type = 'CONSTRAINT'
        joint_obj_handle.ambf_constraint_type = joint_type

        joint_obj_handle.ambf_constraint_parent = parent_obj_handle
        joint_obj_handle.ambf_constraint_child = child_obj_handle

    def set_blender_constraint_params(self, joint_obj_handle, joint_data):
        # If the adjust body pivots and axis was set, the offset angle
        # has already been incorporated, so set it to zero, otherwise
        # get the reading from the ADF
        if self._context.scene.adjust_joint_pivots:
            offset_angle = 0
        else:
            offset_angle = self.get_joint_offset_angle(joint_data)

        if 'joint limits' in joint_data:
            if joint_data['type'] == 'revolute':
                joint_obj_handle.rigid_body_constraint.limit_ang_z_upper \
                    = joint_data['joint limits']['high'] + offset_angle
                joint_obj_handle.rigid_body_constraint.limit_ang_z_lower \
                    = joint_data['joint limits']['low'] + offset_angle
                joint_obj_handle.rigid_body_constraint.use_limit_ang_z = True
            elif joint_data['type'] == 'prismatic':
                joint_obj_handle.rigid_body_constraint.limit_lin_x_upper = joint_data['joint limits']['high']
                joint_obj_handle.rigid_body_constraint.limit_lin_x_lower = joint_data['joint limits']['low']
                joint_obj_handle.rigid_body_constraint.use_limit_lin_x = True

            if joint_data['type'] in ['spring', 'linear spring']:
                joint_obj_handle.rigid_body_constraint.limit_lin_x_upper \
                    = joint_data['joint limits']['high'] + offset_angle
                joint_obj_handle.rigid_body_constraint.limit_lin_x_lower \
                    = joint_data['joint limits']['low'] + offset_angle
                joint_obj_handle.rigid_body_constraint.use_limit_lin_x = True
                if 'damping' in joint_data:
                    _damping = joint_data['damping']
                    joint_obj_handle.rigid_body_constraint.spring_damping_x = _damping
                    joint_obj_handle.rigid_body_constraint.use_spring_x = True
                if 'stiffness' in joint_data:
                    _stiffness = joint_data['stiffness']
                    joint_obj_handle.rigid_body_constraint.spring_stiffness_x = _stiffness
                    joint_obj_handle.rigid_body_constraint.use_spring_x = True

            if joint_data['type'] in ['angular spring', 'torsional spring', 'torsion spring']:
                joint_obj_handle.rigid_body_constraint.limit_ang_z_upper \
                    = joint_data['joint limits']['high'] + offset_angle
                joint_obj_handle.rigid_body_constraint.limit_ang_z_lower \
                    = joint_data['joint limits']['low'] + offset_angle
                joint_obj_handle.rigid_body_constraint.use_limit_ang_z = True
                if 'damping' in joint_data:
                    _damping = joint_data['damping']
                    joint_obj_handle.rigid_body_constraint.spring_damping_ang_z = _damping
                    joint_obj_handle.rigid_body_constraint.use_spring_ang_z = True
                if 'stiffness' in joint_data:
                    _stiffness = joint_data['stiffness']
                    joint_obj_handle.rigid_body_constraint.spring_stiffness_ang_z = _stiffness
                    joint_obj_handle.rigid_body_constraint.use_spring_ang_z = True
            elif joint_data['type'] == 'continuous':
                # Do nothing, not enable the limits
                pass

            # If joint controller is defined. Set the corresponding values in the joint properties
            if 'controller' in joint_data:
                if joint_data['type'] in ['hinge', 'continuous', 'revolute', 'slider', 'prismatic']:
                    self._context.object.ambf_enable_joint_props = True
                    self._context.object.ambf_joint_controller_p_gain = joint_data["controller"]["P"]
                    self._context.object.ambf_joint_controller_d_gain = joint_data["controller"]["D"]

    def set_ambf_constraint_params(self, joint_obj_handle, joint_data):
        limits_defined = False
        joint_obj_handle.ambf_constraint_name = joint_data['name']
        joint_type = self.get_ambf_joint_type(joint_data)
        if 'joint limits' in joint_data:
            if 'low' in joint_data['joint limits'] and 'high' in joint_data['joint limits']:
                joint_obj_handle.ambf_constraint_limits_lower = joint_data['joint limits']['low']
                joint_obj_handle.ambf_constraint_limits_higher = joint_data['joint limits']['high']
                limits_defined = True
                
        self.set_default_ambf_constraint_axis(joint_obj_handle)

        if not limits_defined:
            joint_obj_handle.ambf_constraint_limits_enable = False

        if 'damping' in joint_data:
            joint_obj_handle.ambf_constraint_damping = joint_data['damping']

        if 'stiffness' in joint_data:
            if joint_type in ['LINEAR_SPRING', 'TORSION_SPRING']:
                joint_obj_handle.ambf_constraint_stiffness = joint_data['stiffness']

        # If joint controller is defined. Set the corresponding values in the joint properties
        if 'controller' in joint_data:
            if joint_type in ['REVOLUTE', 'PRISMATIC']:
                joint_obj_handle.ambf_constraint_enable_controller_gains = True
                joint_obj_handle.ambf_constraint_controller_p_gain = joint_data["controller"]["P"]
                joint_obj_handle.ambf_constraint_controller_d_gain = joint_data["controller"]["D"]

    def load_blender_joint(self, joint_name):
        joint_data = self._ambf_data[joint_name]
        select_all_objects(False)
        self._context.scene.objects.active = None
        # Set joint type to blender appropriate name

        parent_obj_handle, child_obj_handle = self.get_parent_and_child_object_handles(joint_data)
        joint_obj_handle = self.get_blender_joint_handle(joint_data)

        T_c_p = self.get_child_in_parent_transform(parent_obj_handle, joint_data)
        # Set the child body the pose calculated above
        joint_obj_handle.matrix_world = T_c_p

        self.make_obj1_parent_of_obj2(obj1=parent_obj_handle, obj2=joint_obj_handle)

        self.create_blender_constraint(joint_obj_handle, self.get_blender_joint_type(joint_data), parent_obj_handle, child_obj_handle)

        self.set_blender_constraint_params(joint_obj_handle, joint_data)

        CommonConfig.loaded_joint_map[child_obj_handle.rigid_body_constraint] = joint_data

    def load_ambf_joint(self, joint_name):
        joint_data = self._ambf_data[joint_name]
        select_all_objects(False)
        self._context.scene.objects.active = None
        # Set joint type to blender appropriate name

        parent_obj_handle, child_obj_handle = self.get_parent_and_child_object_handles(joint_data)
        joint_obj_handle = self.get_ambf_joint_handle(joint_data)

        T_j_p = self.get_joint_in_parent_transform(parent_obj_handle, joint_data)
        T_c_p = self.get_child_in_parent_transform(parent_obj_handle, joint_data)
        # Set the child body the pose calculated above
        joint_obj_handle.matrix_world = T_j_p
        child_obj_handle.matrix_world = T_c_p

        self.make_obj1_parent_of_obj2(obj1=parent_obj_handle, obj2=joint_obj_handle)
        self.make_obj1_parent_of_obj2(obj1=joint_obj_handle, obj2=child_obj_handle)

        self.create_ambf_constraint(joint_obj_handle, self.get_ambf_joint_type(joint_data), parent_obj_handle,
                                       child_obj_handle)

        self.set_ambf_constraint_params(joint_obj_handle, joint_data)

        CommonConfig.loaded_joint_map[child_obj_handle.rigid_body_constraint] = joint_data

    def execute(self, context):
        self._yaml_filepath = str(bpy.path.abspath(context.scene['external_ambf_yaml_filepath']))
        print(self._yaml_filepath)
        yaml_file = open(self._yaml_filepath)
        self._ambf_data = yaml.load(yaml_file)
        self._context = context

        bodies_list = self._ambf_data['bodies']
        joints_list = self._ambf_data['joints']

        if 'namespace' in self._ambf_data:
            set_global_namespace(context, self._ambf_data['namespace'])
        else:
            set_global_namespace(context, '/ambf/env/')

        # num_bodies = len(bodies_list)
        # print('Number of Bodies Specified = ', num_bodies)

        self._high_res_path = self.get_qualified_path(self._ambf_data['high resolution path'])
        # print(self._high_res_path)
        for body_name in bodies_list:
            self.load_body(body_name)

        if context.scene.enable_legacy_loading and context.scene.adjust_joint_pivots:
            self.adjust_body_pivots_and_axis()

        for joint_name in joints_list:
            if context.scene.enable_legacy_loading:
                self.load_blender_joint(joint_name)    
            else:
                self.load_ambf_joint(joint_name)

        # print('Printing Blender Remapped Body Names')
        # print(self._blender_remapped_body_names)
        return {'FINISHED'}


class AMBF_PT_create_ambf(bpy.types.Panel):
    """Creates a Panel in the Tool Shelf"""
    bl_label = "AF FILE CREATION"
    bl_idname = "ambf.create_ambf"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'TOOLS'
    bl_category = "AMBF"

    bpy.types.Scene.ambf_yaml_conf_path = bpy.props.StringProperty \
        (
            name="Config (Save To)",
            default="",
            description="Define the root path of the project",
            subtype='FILE_PATH'
        )

    bpy.types.Scene.ambf_yaml_mesh_path = bpy.props.StringProperty \
        (
            name="Meshes (Save To)",
            default="",
            description = "Define the path to save to mesh files",
            subtype='DIR_PATH'
        )

    bpy.types.Scene.mesh_output_type = bpy.props.EnumProperty \
        (
            items=
            [
                ('STL', 'STL', 'STL'),
                ('OBJ', 'OBJ', 'OBJ'),
                ('3DS', '3DS', '3DS'),
                ('PLY', 'PLY', 'PLY')
            ],
            name="Mesh Type",
            default='STL'
        )

    bpy.context.scene['mesh_output_type'] = 0

    bpy.types.Scene.mesh_max_vertices = bpy.props.IntProperty \
        (
            name="",
            default=150,
            description="The maximum number of vertices the low resolution collision mesh is allowed to have",
        )

    bpy.types.Scene.enable_legacy_loading = bpy.props.BoolProperty \
        (
            name="Enable Legacy Load",
            default=False,
            description="Enable Legacy Loading of ADF Files",
        )

    bpy.types.Scene.adjust_joint_pivots = bpy.props.BoolProperty \
        (
            name="Adjust Child Pivots",
            default=False,
            description="If the child axis is offset from the joint axis, correct for this offset, keep this to "
                        "default (True) unless you want to debug the model or something advanced",
        )

    bpy.types.Scene.ignore_ambf_joint_offsets = bpy.props.BoolProperty \
        (
            name="Ignore Offsets",
            default=False,
            description="Ignore the joint offsets from ambf yaml file, keep this to default (False) "
                        "unless you want to debug the model or something advanced",
        )

    bpy.types.Scene.ignore_inter_collision = bpy.props.BoolProperty \
        (
            name="Ignore Inter-Collision",
            default=True,
            description="Ignore collision between all the bodies in the scene (default = True)",
        )

    bpy.types.Scene.external_ambf_yaml_filepath = bpy.props.StringProperty \
        (
            name="AMBF Config",
            default="",
            description="Load AMBF YAML FILE",
            subtype='FILE_PATH'
        )

    bpy.types.Scene.ambf_namespace = bpy.props.StringProperty \
        (
            name="AMBF Namespace",
            default="/ambf/env/",
            description="The namespace for all bodies in this scene"
        )

    setup_yaml()

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        row = box.row()
        # Panel Label
        row.label(text="Step 1: GENERATE LOW-RES MESH MODIFIERS FOR COLLISION")

        # Mesh Reduction Ratio Properties
        row = box.row(align=True)
        row.alignment = 'LEFT'
        split = row.split(percentage=0.7)
        row = split.row()
        row.label('Coll Mesh Max Verts: ')
        row = split.row()
        row.prop(context.scene, 'mesh_max_vertices')

        # Low Res Mesh Modifier Button
        col = box.column()
        col.alignment = 'CENTER'
        col.operator("ambf.generate_low_res_mesh_modifiers")

        # Panel Label
        row = box.row()
        box.label(text="Step 2: SELECT LOCATION AND SAVE MESHES")

        # Meshes Save Location
        col = box.column()
        col.prop(context.scene, 'ambf_yaml_mesh_path')

        # Select the Mesh-Type for saving the meshes
        col = box.column()
        col.alignment = 'CENTER'
        col.prop(context.scene, 'mesh_output_type')

        # Meshes Save Button
        col = box.column()
        col.alignment = 'CENTER'
        col.operator("ambf.save_meshes")

        row = box.row()
        row.label(text="Step 3: GENERATE ADF")

        row = box.row()
        row.label(text="Step 3a: ADF PATH")
        # Config File Save Location
        col = box.column()
        col.prop(context.scene, 'ambf_yaml_conf_path')
        # AMBF Namespace
        col = box.column()
        col.alignment = 'CENTER'
        col.prop(context.scene, 'ambf_namespace')
        # Config File Save Button
        row = box.row()
        row.label(text="Step 3a: SAVE ADF")

        # Ignore Inter Collision Button
        col = box.column()
        col.alignment = 'CENTER'
        col.prop(context.scene, "ignore_inter_collision")

        col = box.column()
        col.alignment = 'CENTER'
        col.operator("ambf.add_generate_ambf_file")

        box = layout.box()
        row = box.row()
        row.label(text="OPTIONAL :")

        row = box.row()
        # Column for creating detached joint
        col = box.column()
        col.alignment = 'CENTER'
        col.operator("ambf.remove_object_namespaces")

        # Column for creating detached joint
        col = box.column()
        col.alignment = 'CENTER'
        col.operator("ambf.create_detached_joint")

        # Add Optional Button to Remove All Modifiers
        col = box.column()
        col.alignment = 'CENTER'
        col.operator("ambf.remove_low_res_mesh_modifiers")

        # Add Optional Button to Toggle the Visibility of Low-Res Modifiers
        col = box.column()
        col.alignment = 'CENTER'
        col.operator("ambf.toggle_low_res_mesh_modifiers_visibility")

        box = layout.box()
        row = box.row()
        # Load AMBF File Into Blender
        row.label(text="LOAD AMBF FILE :")

        # Load
        col = box.column()
        col.alignment = 'CENTER'
        col.prop(context.scene, 'external_ambf_yaml_filepath')
        
        col = box.column()
        col.alignment = 'CENTER'
        col.operator("ambf.load_ambf_file")

        box = layout.box()

        # Enable Legacy Loading
        col = box.column()
        col.alignment = 'CENTER'
        col.prop(context.scene, 'enable_legacy_loading')

        # Load
        col = box.column()
        col.alignment = 'CENTER'
        col.enabled = context.scene.enable_legacy_loading
        col.prop(context.scene, 'adjust_joint_pivots')

        # Load
        col = box.column()
        col.alignment = 'CENTER'
        col.enabled = context.scene.enable_legacy_loading
        col.prop(context.scene, 'ignore_ambf_joint_offsets')


class AMBF_PT_rigid_body_props(bpy.types.Panel):
    """Add Rigid Body Properties"""
    bl_label = "AMBF RIGID BODY ADDITIONAL PROPERTIES"
    bl_idname = "ambf.rigid_body_props"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context= "physics"
    
    bpy.types.Object.ambf_enable_body_props = bpy.props.BoolProperty(name="Enable", default=False)
    
    bpy.types.Object.ambf_linear_controller_p_gain = bpy.props.FloatProperty(name="Proportional Gain (P)", default=500, min=0)
    bpy.types.Object.ambf_linear_controller_d_gain = bpy.props.FloatProperty(name="Damping Gain (D)", default=5, min=0)
    
    bpy.types.Object.ambf_angular_controller_p_gain = bpy.props.FloatProperty(name="Proportional Gain (P)", default=50, min=0)
    bpy.types.Object.ambf_angular_controller_d_gain = bpy.props.FloatProperty(name="Damping Gain (D)", default=0.5, min=0)
    
    @classmethod
    def poll(self, context):
        active = False
        if context.active_object:
            if context.active_object.type == 'MESH':
                if context.active_object.rigid_body:
                    active=True
        return active
    
    def draw(self, context):
        layout = self.layout
        
        col = layout.column()
        col.prop(context.object, 'ambf_enable_body_props')
        
        col = layout.column()
        col.alignment = 'CENTER'
        col.enabled = context.object.ambf_enable_body_props
        col.label(text="BODY CONTROLLER GAINS")

        col = col.column()
        col.alignment = 'CENTER'
        col.label(text="LINEAR GAINS:")
        
        row = col.row()
        row.prop(context.object, 'ambf_linear_controller_p_gain')
        
        row = row.row()
        row.prop(context.object, 'ambf_linear_controller_d_gain')
        
        col = col.column()
        col.alignment = 'CENTER'
        col.label(text="ANGULAR GAINS")
        
        row = col.row()
        row.prop(context.object, 'ambf_angular_controller_p_gain')
        
        row = row.row()
        row.prop(context.object, 'ambf_angular_controller_d_gain')
        
        
class AMBF_PT_joint_props(bpy.types.Panel):
    """Add Rigid Body Properties"""
    bl_label = "AMBF JOINT ADDITIONAL PROPERTIES"
    bl_idname = "ambf.joint_props"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context= "physics"

    bpy.types.Object.ambf_enable_joint_props = bpy.props.BoolProperty(name="Enable", default=False)
    bpy.types.Object.ambf_joint_controller_p_gain = bpy.props.FloatProperty(name="Proportional Gain (P)", default=500, min=0)
    bpy.types.Object.ambf_joint_controller_d_gain = bpy.props.FloatProperty(name="Damping Gain (D)", default=5, min=0)
    bpy.types.Object.ambf_joint_damping = bpy.props.FloatProperty(name="Joint Damping", default=0.0, min=0.0)
    
    @classmethod
    def poll(self, context):
        has_detached_prefix = False
        if context.active_object: # Check if an object is active
            if context.active_object.type in ['EMPTY', 'MESH']: # Check if the object is a mesh or an empty axis
                if context.active_object.rigid_body_constraint: # Check if the object has a constraint
                    if context.active_object.rigid_body_constraint.type in ['HINGE', 'SLIDER', 'GENERIC']: # Check if a valid constraint
                        has_detached_prefix = True
        return has_detached_prefix
    
    def draw(self, context):
        layout = self.layout
        
        col = layout.column()
        col.prop(context.object, 'ambf_enable_joint_props')
 
        col = layout.column()
        col.alignment = 'CENTER'
        col.enabled = context.object.ambf_enable_joint_props
        col.prop(context.object, 'ambf_joint_damping')
        
        col.label(text="JOINT CONTROLLER GAINS")
        
        row = col.row()
        row.prop(context.object, 'ambf_joint_controller_p_gain')
        
        row = row.row()
        row.prop(context.object, 'ambf_joint_controller_d_gain')


class AMBF_OT_ambf_rigid_body_activate(bpy.types.Operator):
    """Add Rigid Body Properties"""
    bl_label = "AMBF RIGID BODY ACTIVATE"
    bl_idname = "ambf.ambf_rigid_body_activate"
    
    def execute(self, context):
        context.object.ambf_rigid_body_enable = not context.object.ambf_rigid_body_enable
        
        if context.object.ambf_rigid_body_enable:
            context.object.ambf_object_type = 'RIGID_BODY'
        else:
            context.object.ambf_object_type = 'NONE'
            
        return {'FINISHED'}
    
        
class AMBF_PT_ambf_rigid_body(bpy.types.Panel):
    """Add Rigid Body Properties"""
    bl_label = "AMBF RIGID BODY PROPERTIES"
    bl_idname = "ambf.ambf_rigid_body"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context= "physics"
    
    bpy.types.Object.ambf_rigid_body_enable = bpy.props.BoolProperty(name="Enable AMBF Rigid Body", default=False)
    
    bpy.types.Object.ambf_rigid_body_mass = bpy.props.FloatProperty(name="mass", default=1.0, min=0.001)
    
    bpy.types.Object.ambf_rigid_body_friction = bpy.props.FloatProperty(name="Friction", default=0.5, min=0.0, max=1.0)
    
    bpy.types.Object.ambf_rigid_body_restitution = bpy.props.FloatProperty(name="Restitution", default=0.1, min=0.0, max=1.0)
    
    bpy.types.Object.ambf_rigid_body_enable_collision_margin = bpy.props.BoolProperty(name="Collision Margin", default=False)
    
    bpy.types.Object.ambf_rigid_body_collision_margin = bpy.props.FloatProperty(name="Margin", default=0.001, min=-0.1, max=1.0)
    
    bpy.types.Object.ambf_rigid_body_linear_damping = bpy.props.FloatProperty(name="Linear Damping", default=0.5, min=0.0, max=1.0)
    
    bpy.types.Object.ambf_rigid_body_angular_damping = bpy.props.FloatProperty(name="Angular Damping", default=0.1, min=0.0, max=1.0)

    bpy.types.Object.ambf_rigid_body_enable_controllers = bpy.props.BoolProperty(name="Enable Controllers", default=False)

    bpy.types.Object.ambf_rigid_body_linear_controller_p_gain = bpy.props.FloatProperty(name="Proportional Gain (P)", default=500, min=0)

    bpy.types.Object.ambf_rigid_body_linear_controller_d_gain = bpy.props.FloatProperty(name="Damping Gain (D)", default=5, min=0)

    bpy.types.Object.ambf_rigid_body_angular_controller_p_gain = bpy.props.FloatProperty(name="Proportional Gain (P)", default=50, min=0)

    bpy.types.Object.ambf_rigid_body_angular_controller_d_gain = bpy.props.FloatProperty(name="Damping Gain (D)", default=0.5, min=0)

    bpy.types.Object.ambf_rigid_body_is_static = bpy.props.BoolProperty \
        (
            name="Static",
            default=False,
            description="Is this object dynamic or static (mass = 0.0 Kg)"
        )

    bpy.types.Object.ambf_rigid_body_collision_shape = bpy.props.EnumProperty \
        (
            items=
            [
                ('CONVEX_HULL', 'Convex Hull', '', 'MESH_ICOSPHERE', 0),
                ('CONE', 'Cone', '', 'MESH_CONE', 1),
                ('CYLINDER', 'Cylinder', '', 'MESH_CYLINDER', 2),
                ('CAPSULE', 'Capsule', '', 'MESH_CAPSULE', 3),
                ('SPHERE', 'Sphere', '', 'MESH_UVSPHERE', 4),
                ('BOX', 'Box', '', 'MESH_CUBE', 5),
            ],
            name="Collision Shape",
            default="CONVEX_HULL"
        )
    
    bpy.types.Object.ambf_rigid_body_collision_groups = bpy.props.BoolVectorProperty \
        (
            name='Collision Groups',
            size=20,
            default=(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0),
            options={'PROPORTIONAL'},
            subtype='LAYER'
        )
    
    bpy.types.Object.ambf_rigid_body_linear_inertial_offset = bpy.props.FloatVectorProperty \
        (
            name='Linear Inertial Offset',
            default=(0.0, 0.0, 0.0),
            options={'PROPORTIONAL'},
            subtype='XYZ',
            min=0.0
        )
    
    bpy.types.Object.ambf_rigid_body_angular_inertial_offset = bpy.props.FloatVectorProperty \
        (
            name='Angular Inertial Offset',
            default=(0.0, 0.0, 0.0),
            options={'PROPORTIONAL'},
            subtype='EULER',
            min=0.0
        )
    
    bpy.types.Object.ambf_object_type = bpy.props.EnumProperty \
        (
            name="Object Type",
            items=
            [
                ('NONE', 'None', '', '', 0),
                ('RIGID_BODY', 'RIGID_BODY', '', '', 1),
                ('CONSTRAINT', 'CONSTRAINT', '', '', 2),
            ],
            default='NONE'
        )
    
    @classmethod
    def poll(self, context):
        active = False
        if context.active_object: # Check if an object is active
            if context.active_object.type in ['EMPTY', 'MESH']:
                active = True
                
        return active
    
    def draw(self, context):
        layout = self.layout
        
        col = layout.row()
        col.alignment = 'EXPAND'
        col.scale_y = 2
        col.operator('ambf.ambf_rigid_body_activate', text='Enable AMBF Rigid Body', icon='RNA_ADD')
        
        if context.object.ambf_rigid_body_enable: 
            layout.separator() 
            layout.separator()           
            row = layout.row()
            row.prop(context.object, 'ambf_rigid_body_is_static', toggle=True)
            
            col = row.row()
            col.enabled = not context.object.ambf_rigid_body_is_static
            col.alignment = 'EXPAND'
            col.prop(context.object, 'ambf_rigid_body_mass')
            
            layout.separator()
            
            row = layout.row()
            row.prop(context.object, 'ambf_rigid_body_collision_shape')
            
            layout.separator()
            
            row = layout.row()
            row.prop(context.object, 'ambf_rigid_body_enable_collision_margin', toggle=True)
            
            row = row.row()
            row.enabled = context.object.ambf_rigid_body_enable_collision_margin
            row.prop(context.object, 'ambf_rigid_body_collision_margin')
            
            layout.separator()
            
            row = layout.column()
            row.alignment = 'EXPAND'
            row.prop(context.object, 'ambf_rigid_body_collision_groups', toggle=True)
            
            layout.separator()
            
            row = layout.row()
            row.prop(context.object, 'ambf_rigid_body_friction')
            
            row = layout.row()
            row.prop(context.object, 'ambf_rigid_body_restitution')
            
            layout.separator()
            
            row = layout.row()
            row.prop(context.object, 'ambf_rigid_body_linear_damping')
            
            row = layout.row()
            row.prop(context.object, 'ambf_rigid_body_angular_damping')
            
            col = layout.column()
            col = col.split(percentage=0.5)
            col.alignment = 'EXPAND'
            col.prop(context.object, 'ambf_rigid_body_linear_inertial_offset')
            
            col = col.column()
            col.enabled = False
            col.alignment = 'EXPAND'
            col.prop(context.object, 'ambf_rigid_body_angular_inertial_offset')
            
            # Rigid Body Controller Properties
            row = layout.row()
            row.alignment = 'CENTER'
            row.prop(context.object, 'ambf_rigid_body_enable_controllers', toggle=True)
            row.scale_y=2
        
            col = layout.column()
            col.label('Linear Gains')
            
            col = layout.column()
            col.enabled = context.object.ambf_rigid_body_enable_controllers
            row = col.row()
            row.prop(context.object, 'ambf_rigid_body_linear_controller_p_gain', text='P')
        
            row = row.row()
            row.prop(context.object, 'ambf_rigid_body_linear_controller_d_gain', text='D')
            
            col = layout.column()
            col.label('Angular Gains')
            
            col = layout.column()
            col.enabled = context.object.ambf_rigid_body_enable_controllers
            row = col.row()
            row.prop(context.object, 'ambf_rigid_body_angular_controller_p_gain', text='P')
        
            row = row.row()
            row.prop(context.object, 'ambf_rigid_body_angular_controller_d_gain', text='D')
            
            
class AMBF_OT_ambf_constraint_activate(bpy.types.Operator):
    """Add Rigid Body Properties"""
    bl_label = "AMBF CONSTRAINT ACTIVATE"
    bl_idname = "ambf.ambf_constraint_activate"
    
    def execute(self, context):
        context.object.ambf_constraint_enable = not context.object.ambf_constraint_enable
        
        if context.object.ambf_constraint_enable:
            context.object.ambf_object_type = 'CONSTRAINT'
        else:
            context.object.ambf_object_type = 'NONE'
            
        
        return {'FINISHED'}
            

class AMBF_PT_ambf_constraint(bpy.types.Panel):
    """Add Rigid Body Properties"""
    bl_label = "AMBF CONSTRAINT PROPERTIES"
    bl_idname = "ambf.ambf_constraint"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context= "physics"
    
    bpy.types.Object.ambf_constraint_enable = bpy.props.BoolProperty(name="Enable", default=False)
    
    bpy.types.Object.ambf_constraint_parent = bpy.props.PointerProperty(name="Parent", type=bpy.types.Object)
    
    bpy.types.Object.ambf_constraint_child = bpy.props.PointerProperty(name="Child", type=bpy.types.Object)
    
    bpy.types.Object.ambf_constraint_name = bpy.props.StringProperty(name="Name", default="")
    
    bpy.types.Object.ambf_constraint_enable_controller_gains = bpy.props.BoolProperty(name="Enable Controller Gains", default=False)
    
    bpy.types.Object.ambf_constraint_controller_p_gain = bpy.props.FloatProperty(name="Proportional Gain (P)", default=500, min=0)
    
    bpy.types.Object.ambf_constraint_controller_d_gain = bpy.props.FloatProperty(name="Damping Gain (D)", default=5, min=0)
    
    bpy.types.Object.ambf_constraint_damping = bpy.props.FloatProperty(name="Joint Damping", default=0.0, min=0.0)

    bpy.types.Object.ambf_constraint_stiffness = bpy.props.FloatProperty(name="Joint Stiffness", default=0.0, min=0.0)

    bpy.types.Object.ambf_constraint_limits_enable = bpy.props.BoolProperty(name="Enable Limits", default=True)

    bpy.types.Object.ambf_constraint_limits_lower = bpy.props.FloatProperty(name="Low", default=math.radians(-60), min=-3.14, max=3.14, unit='ROTATION')
    bpy.types.Object.ambf_constraint_limits_higher = bpy.props.FloatProperty(name="High", default=math.radians(60), min=-3.14, max=3.14, unit='ROTATION')
    
    bpy.types.Object.ambf_constraint_axis = bpy.props.EnumProperty \
        (
            name='Axis',
            items=
            [
                ('X', 'X', '', '', 0),
                ('Y', 'Y', '', '', 1),
                ('Z', 'Z', '', '', 2),
            ],
            default='Z'
        )
    
    bpy.types.Object.ambf_constraint_type = bpy.props.EnumProperty \
        (
            items=
            [
                ('FIXED', 'Fixed', '', '', 0),
                ('REVOLUTE', 'Revolute', '', '', 1),
                ('PRISMATIC', 'Prismatic', '', '', 2),
                ('LINEAR_SPRING', 'Linear Spring', '', '', 3),
                ('TORSION_SPRING', 'Torsion Spring', '', '', 4),
                ('P2P', 'p2p', '', '', 5),
            ],
            name="Type",
            default='REVOLUTE'
        )
    
    @classmethod
    def poll(self, context):
        active = False
        if context.active_object: # Check if an object is active
            if context.active_object.type in ['EMPTY']:
                active = True          
        return active
    
    def draw(self, context):
        
        layout = self.layout
        
        row = layout.row()
        row.alignment = 'EXPAND'
        row.operator('ambf.ambf_constraint_activate', text='Enable AMBF Constraint', icon='FORCE_HARMONIC')
        row.scale_y = 2
        
        if context.object.ambf_constraint_enable:
            
            col = layout.column()
            col.alignment = 'CENTER'
            col.prop(context.object, 'ambf_constraint_name')
            
            col = layout.column()
            col.prop(context.object, 'ambf_constraint_type')
            
            col = layout.column()
            col.prop_search(context.object, "ambf_constraint_parent", context.scene, "objects")
            
            col = layout.column()
            col.prop_search(context.object, "ambf_constraint_child", context.scene, "objects")
            
            layout.separator()
            layout.separator()
            
            if context.object.ambf_constraint_type in ['PRISMATIC', 'REVOLUTE', 'LINEAR_SPRING', 'TORSION_SPRING']:
                row = layout.row()
                row.alignment = 'EXPAND'
                row.prop(context.object, 'ambf_constraint_axis')

                row = layout.row()
                row.prop(context.object, 'ambf_constraint_damping')
                row.scale_y=1.5

                if context.object.ambf_constraint_type in ['LINEAR_SPRING', 'TORSION_SPRING']:
                    row = layout.row()
                    row.prop(context.object, 'ambf_constraint_stiffness')
                    row.scale_y=1.5
                
                row = layout.row()
                row.alignment = 'CENTER'
                row.prop(context.object, 'ambf_constraint_limits_enable', toggle=True)
                row.scale_y=2
                
                col = layout.column()
                col.enabled = context.object.ambf_constraint_limits_enable
                col.prop(context.object, 'ambf_constraint_limits_lower', text='Low')
                
                col = col.column()
                col.prop(context.object, 'ambf_constraint_limits_higher', text='High')
 
                if context.object.ambf_constraint_type in ['PRISMATIC', 'REVOLUTE']:
                    row = layout.row()
                    row.alignment = 'CENTER'
                    row.prop(context.object, 'ambf_constraint_enable_controller_gains', toggle=True, text='Enable Gains')
                    row.scale_y=2
        
                    col = layout.column()
                    col.enabled = context.object.ambf_constraint_enable_controller_gains
                    col.prop(context.object, 'ambf_constraint_controller_p_gain', text='P')
        
                    col = col.column()
                    col.prop(context.object, 'ambf_constraint_controller_d_gain', text='D')


custom_classes = (AMBF_OT_toggle_low_res_mesh_modifiers_visibility,
                  AMBF_OT_remove_low_res_mesh_modifiers,
                  AMBF_OT_generate_low_res_mesh_modifiers,
                  AMBF_OT_generate_ambf_file,
                  AMBF_OT_save_meshes,
                  AMBF_OT_load_ambf_file,
                  AMBF_PT_create_ambf,
                  AMBF_OT_create_detached_joint,
                  AMBF_OT_remove_object_namespaces,
                  AMBF_OT_ambf_rigid_body_activate,
                  AMBF_PT_ambf_rigid_body,
                  AMBF_OT_ambf_constraint_activate,
                  AMBF_PT_ambf_constraint)


def register():
    from bpy.utils import register_class
    for cls in custom_classes:
        register_class(cls)


def unregister():
    from bpy.utils import unregister_class
    for cls in reversed(custom_classes):
        unregister_class(cls)


if __name__ == "__main__":
    register()
    #unregister()
