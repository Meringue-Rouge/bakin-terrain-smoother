bl_info = {
    "name": "Bakin Terrain Smoother",
    "author": "ingenoire",
    "version": (1, 3),
    "blender": (2, 80, 0),
    "location": "View3D > Sidebar > Bakin Terrain Smoother",
    "description": "Smooths selected voxel terrain areas and selects specific block types based on UV",
    "category": "Mesh",
}

import bpy
import bmesh
import os
import shutil
import random
import numpy as np
from mathutils import Vector
from skimage.measure import marching_cubes
from bpy.props import FloatProperty, IntProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup

class UVTileProperties(PropertyGroup):
    tile_x: IntProperty(name="Tile X", default=0, min=0)
    tile_y: IntProperty(name="Tile Y", default=0, min=0)

class SmoothVoxelTerrainPanel(Panel):
    """Creates a Panel in the 3D Viewport Sidebar"""
    bl_label = "Bakin Terrain Smoother"
    bl_idname = "VIEW3D_PT_bakin_terrain_smoother"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Bakin Terrain Smoother'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        uv_props = scene.uv_tile_props

        layout.prop(scene, "smoothing_factor")
        layout.operator("mesh.smooth_voxel_terrain")

        layout.separator()
        layout.label(text="Select UV Tile:")

        obj = context.active_object
        if obj and obj.type == 'MESH' and obj.active_material:
            mat = obj.active_material
            if mat.use_nodes:
                for node in mat.node_tree.nodes:
                    if node.type == 'TEX_IMAGE' and node.image:
                        layout.template_preview(node.image, show_buttons=False)
                        break
        
        row = layout.row()
        row.prop(uv_props, "tile_x", text="Tile X")
        row.prop(uv_props, "tile_y", text="Tile Y")
        
        layout.separator()
        layout.label(text="Select Tile w ID XY")
        layout.operator("mesh.select_uv_tile")
        
        layout.separator()
        layout.label(text="Auto Sculpt Terrain")
        layout.operator("mesh.auto_sculpt_terrain_smooth")


        layout.separator()
        layout.label(text="Quick Export")
        layout.operator("export.quick_fbx")

        layout.separator()
        layout.label(text="Final Publish Export")
        layout.operator("mesh.separate_by_uv_tiles")

class SmoothVoxelTerrainOperator(Operator):
    """Smooths the selected voxel terrain"""
    bl_idname = "mesh.smooth_voxel_terrain"
    bl_label = "Smooth Voxel Terrain"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        smooth_selected_voxel_terrain(context.scene.smoothing_factor)
        return {'FINISHED'}

def duplicate_texture_folder(original_folder, new_folder):
    """Copies a texture folder without renaming any files, ensuring the new folder is named 'texture'."""
    if not os.path.exists(original_folder):
        print(f"Texture folder not found: {original_folder}")
        return
    
    texture_folder_path = os.path.join(new_folder, "texture")
    os.makedirs(texture_folder_path, exist_ok=True)

    for file_name in os.listdir(original_folder):
        old_path = os.path.join(original_folder, file_name)
        new_path = os.path.join(texture_folder_path, file_name)

        if os.path.isfile(old_path):
            shutil.copy2(old_path, new_path)
            print(f"Copied: {old_path} → {new_path}")


def export_empty_fbx_with_material(obj, export_path):
    """Creates a tiny flat plane with UV mapping and all materials assigned, then exports it as FBX."""
    bpy.ops.object.select_all(action='DESELECT')

    # Create a new plane mesh
    mesh = bpy.data.meshes.new("MaterialPlane")
    mat_plane = bpy.data.objects.new("Split_Root", mesh)
    bpy.context.collection.objects.link(mat_plane)

    # Create the plane geometry (small 0.01x0.01 unit)
    bm = bmesh.new()
    verts = [
        bm.verts.new((-0.005, -0.005, 0)),  # Bottom-left
        bm.verts.new(( 0.005, -0.005, 0)),  # Bottom-right
        bm.verts.new(( 0.005,  0.005, 0)),  # Top-right
        bm.verts.new((-0.005,  0.005, 0))   # Top-left
    ]
    bm.faces.new(verts)
    
    # Add UV layer
    uv_layer = bm.loops.layers.uv.new("UVMap")
    
    # Set UV coordinates (covering full 0-1 range)
    for face in bm.faces:
        face.loops[0][uv_layer].uv = (0.0, 0.0)  # Bottom-left
        face.loops[1][uv_layer].uv = (1.0, 0.0)  # Bottom-right
        face.loops[2][uv_layer].uv = (1.0, 1.0)  # Top-right
        face.loops[3][uv_layer].uv = (0.0, 1.0)  # Top-left

    bm.to_mesh(mesh)
    bm.free()

    # Assign materials from the original object
    for mat in obj.data.materials:
        if mat:
            mat_plane.data.materials.append(mat)

    # Export the plane as FBX
    bpy.ops.object.select_all(action='DESELECT')
    mat_plane.select_set(True)
    bpy.context.view_layer.objects.active = mat_plane

    bpy.ops.export_scene.fbx(filepath=export_path, use_selection=True)

    # Cleanup: Remove the temporary plane
    bpy.data.objects.remove(mat_plane)
    bpy.data.meshes.remove(mesh)

    print(f"Exported material-holding plane FBX with UV: {export_path}")


    

class AutoSculptTerrainOperator(bpy.types.Operator):
    """Smooths selected terrain while preserving flat areas"""
    bl_idname = "mesh.auto_sculpt_terrain_smooth"
    bl_label = "Auto Smooth Terrain"
    bl_options = {'REGISTER', 'UNDO'}

    smooth_iterations: bpy.props.IntProperty(
        name="Smooth Iterations",
        default=10,
        min=1,
        max=100,
        description="Number of times to apply smoothing"
    )

    def execute(self, context):
        self.auto_smooth_terrain(context)
        return {'FINISHED'}

    def auto_smooth_terrain(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "No valid mesh object selected")
            return {'CANCELLED'}

        # Switch to Edit mode
        bpy.ops.object.mode_set(mode='EDIT')
        mesh = obj.data
        bm = bmesh.from_edit_mesh(mesh)

        # Get selected vertices
        selected_verts = [v for v in bm.verts if v.select]
        if not selected_verts:
            self.report({'WARNING'}, "No vertices selected.")
            return {'CANCELLED'}

        # Identify flat surface vertices
        flat_threshold = 0.01  # Tolerance for detecting flat areas
        normals = [v.normal for v in selected_verts]
        avg_normal = sum(normals, bm.verts[0].normal) / len(normals)
        flat_verts = [v for v in selected_verts if (v.normal - avg_normal).length < flat_threshold]

        # Perform Laplacian smoothing (excluding flat areas)
        for _ in range(self.smooth_iterations):
            new_positions = {}
            for v in selected_verts:
                if v in flat_verts:
                    continue  # Skip flat areas

                neighbor_positions = [e.other_vert(v).co for e in v.link_edges]
                avg_pos = sum(neighbor_positions, v.co) / (len(neighbor_positions) + 1)
                new_positions[v] = avg_pos

            # Apply new vertex positions
            for v, new_pos in new_positions.items():
                v.co = new_pos

        # Update mesh
        bmesh.update_edit_mesh(mesh)
        bpy.ops.object.mode_set(mode='OBJECT')

        self.report({'INFO'}, "Terrain smoothed while preserving flat surfaces.")
        return {'FINISHED'}

def menu_func(self, context):
    self.layout.operator(AutoSculptTerrainOperator.bl_idname)

class SeparateMeshByUVTilesOperator(Operator):
    """Separates the mesh into multiple objects based on UV tile selection and exports as FBX"""
    bl_idname = "mesh.separate_by_uv_tiles"
    bl_label = "Export as Split Meshes (Footsteps ON)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = bpy.context.active_object
        
        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "No valid mesh object selected")
            return {'CANCELLED'}
        
        if not bpy.data.filepath:
            self.report({'ERROR'}, "Save the blend file before exporting.")
            return {'CANCELLED'}
        
        blend_path = bpy.data.filepath
        blend_name = os.path.splitext(os.path.basename(blend_path))[0]  
        export_dir = os.path.join(os.path.dirname(blend_path), f"{blend_name} (Split Parts)")
        os.makedirs(export_dir, exist_ok=True)

        texture_source_folder = os.path.join(os.path.dirname(blend_path), f"{blend_name}_texture")
        texture_target_folder = os.path.join(export_dir, f"{blend_name}_split_root_texture")

        # Step 1: Export empty "split root" FBX with materials only
        split_root_path = os.path.join(export_dir, f"{blend_name}_split_root.fbx")
        export_empty_fbx_with_material(obj, split_root_path)

        # Step 2: Duplicate original texture folder for the root
        duplicate_texture_folder(texture_source_folder, texture_target_folder)

        # Step 3: Extract unique UV tile IDs from the UV map
        if obj and obj.type == 'MESH':
            bpy.context.view_layer.objects.active = obj  # Ensure object is active
            obj.select_set(True)  # Ensure object is selected
            bpy.ops.object.mode_set(mode='EDIT')
        else:
            self.report({'WARNING'}, "No valid mesh object selected")
            return {'CANCELLED'}

        mesh = obj.data
        tile_dict = {}
        bm = bmesh.from_edit_mesh(mesh)
        uv_layer = bm.loops.layers.uv.active
        if not uv_layer:
            self.report({'WARNING'}, "No active UV layer found")
            return {'CANCELLED'}
        
        for face in bm.faces:
            for loop in face.loops:
                uv = loop[uv_layer].uv
                tile_x = int(uv.x * 10)
                tile_y = int(uv.y * 10)

                if (tile_x, tile_y) not in tile_dict:
                    tile_dict[(tile_x, tile_y)] = set()
                tile_dict[(tile_x, tile_y)].add(face.index)

        bpy.ops.object.mode_set(mode='OBJECT')

        if not tile_dict:
            self.report({'INFO'}, "No UV tile regions found in the mesh.")
            return {'CANCELLED'}

        # Step 4: Process tiles and export them as FBX
        exported_objects = []
        for tile_x, tile_y in sorted(tile_dict.keys()):
            bpy.ops.object.mode_set(mode='EDIT')
            bm = bmesh.from_edit_mesh(mesh)

            bpy.ops.mesh.select_all(action='DESELECT')

            for face in bm.faces:
                if face.index in tile_dict[(tile_x, tile_y)]:
                    face.select = True

            bmesh.update_edit_mesh(mesh)
            bpy.ops.mesh.duplicate()
            bpy.ops.mesh.separate(type='SELECTED')
            bpy.ops.object.mode_set(mode='OBJECT')

            new_obj = bpy.context.selected_objects[-1]
            new_obj.name = f"{blend_name}_s{tile_x}{tile_y}"
            exported_objects.append(new_obj)

        for new_obj in exported_objects:
            export_path = os.path.join(export_dir, f"{new_obj.name}.fbx")
            #def_path = os.path.join(export_dir, f"{new_obj.name}.def")

            bpy.ops.object.select_all(action='DESELECT')
            new_obj.select_set(True)
            bpy.context.view_layer.objects.active = new_obj
            bpy.ops.export_scene.fbx(filepath=export_path, use_selection=True)

            #open(def_path, 'w').close()

        bpy.ops.object.select_all(action='DESELECT')
        for new_obj in exported_objects:
            new_obj.select_set(True)
        bpy.ops.object.delete()
        obj.hide_set(False)

        self.report({'INFO'}, f"Exported {len(exported_objects)} meshes to {export_dir}.")
        return {'FINISHED'}


class QuickExportFBXOperator(Operator):
    """Quickly exports the original mesh as an FBX file, along with a duplicated texture folder"""
    bl_idname = "export.quick_fbx"
    bl_label = "Quick Export (FBX)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = bpy.context.active_object

        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "No valid mesh object selected")
            return {'CANCELLED'}

        if not bpy.data.filepath:
            self.report({'ERROR'}, "Save the blend file before exporting.")
            return {'CANCELLED'}

        blend_path = bpy.data.filepath
        blend_name = os.path.splitext(os.path.basename(blend_path))[0]  
        export_path = os.path.join(os.path.dirname(blend_path), f"{blend_name}_export.fbx")
        #def_path = os.path.join(os.path.dirname(blend_path), f"{blend_name}_export.def")
        texture_source_folder = os.path.join(os.path.dirname(blend_path), f"{blend_name}_texture")
        texture_target_folder = os.path.join(os.path.dirname(blend_path), f"{blend_name}_export_texture")

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        bpy.ops.export_scene.fbx(filepath=export_path, use_selection=True)
        #open(def_path, 'w').close()

        # Duplicate original texture folder
        duplicate_texture_folder(texture_source_folder, texture_target_folder)

        self.report({'INFO'}, f"Quick Export completed: {export_path}")
        return {'FINISHED'}

class SelectUVTileOperator(Operator):
    """Selects vertices mapped to a given UV tile, including all overlapping islands"""
    bl_idname = "mesh.select_uv_tile"
    bl_label = "Select UV Tile"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = bpy.context.active_object
        uv_props = context.scene.uv_tile_props
        
        if not obj or obj.type != 'MESH':
            self.report({'WARNING'}, "No valid mesh object selected")
            return {'CANCELLED'}
        
        bpy.ops.object.mode_set(mode='EDIT')
        mesh = obj.data
        bm = bmesh.from_edit_mesh(mesh)

        uv_layer = bm.loops.layers.uv.active
        if not uv_layer:
            self.report({'WARNING'}, "No active UV layer found")
            return {'CANCELLED'}
        
        mat = obj.active_material
        texture = None
        if mat and mat.use_nodes:
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    texture = node.image
                    break
        
        if not texture:
            self.report({'WARNING'}, "No texture image found")
            return {'CANCELLED'}
        
        tex_width, tex_height = texture.size[:2]
        tile_x, tile_y = uv_props.tile_x, uv_props.tile_y

        print(f"Texture size: {tex_width}x{tex_height}")

        # **Corrected UV Calculation**
        tile_size = 128  # Each tile is 128px
        padding = 32      # 32px padding around each tile

        # Number of tiles per row/column
        num_tiles_x = (tex_width - padding) // (tile_size + padding)
        num_tiles_y = (tex_height - padding) // (tile_size + padding)

        # Ensure tile selection is within valid bounds
        if tile_x >= num_tiles_x or tile_y >= num_tiles_y:
            self.report({'WARNING'}, f"Tile ({tile_x}, {tile_y}) is out of bounds for texture size {tex_width}x{tex_height}")
            return {'CANCELLED'}

        # UV coordinates (normalized to 0-1 range)
        uv_x_min = (padding + tile_x * (tile_size + padding)) / tex_width
        uv_x_max = (padding + (tile_x + 1) * tile_size + tile_x * padding) / tex_width
        uv_y_min = 1 - ((padding + (tile_y + 1) * tile_size + tile_y * padding) / tex_height)
        uv_y_max = 1 - ((padding + tile_y * (tile_size + padding)) / tex_height)

        print(f"Selecting UVs in Tile ({tile_x}, {tile_y}): X({uv_x_min:.4f} - {uv_x_max:.4f}), Y({uv_y_min:.4f} - {uv_y_max:.4f})")

        # **Selection Logic**
        selected_count = 0
        for face in bm.faces:
            face_selected = False
            for loop in face.loops:
                uv = loop[uv_layer].uv
                if uv_x_min <= uv.x <= uv_x_max and uv_y_min <= uv.y <= uv_y_max:
                    face_selected = True
                    break

            if face_selected:
                for v in face.verts:
                    v.select = True  # Add to selection
                selected_count += 1

        print(f"Number of faces selected: {selected_count}")
        bmesh.update_edit_mesh(mesh)

        if selected_count == 0:
            self.report({'INFO'}, "No UVs were selected. Check UV layout.")
        
        return {'FINISHED'}



def smooth_selected_voxel_terrain(smoothing_factor):
    obj = bpy.context.active_object
    bpy.ops.object.mode_set(mode='EDIT')  # Ensure we are in edit mode
    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)

    selected_verts = [v for v in bm.verts if v.select]

    if not selected_verts:
        print("No vertices selected. Skipping smoothing.")
        return

    for v in selected_verts:
        neighbors = []
        for e in v.link_edges:
            other_v = e.other_vert(v)
            if other_v.select and (v.co - other_v.co).length < 1.5:  # Adjust for voxel size
                neighbors.append(other_v.co)

        if neighbors:
            # Calculate the height gradient (focus on the Z-axis)
            avg_pos = Vector((sum(n.x for n in neighbors) / len(neighbors),
                              sum(n.y for n in neighbors) / len(neighbors),
                              sum(n.z for n in neighbors) / len(neighbors)))
            
            # Apply a hill-like smoothing effect by adjusting the Z-coordinate more significantly
            # The closer the neighbor, the more it influences the smoothing
            distance_factors = [1 / (v.co - n).length for n in neighbors]  # Inverse distance as weight
            total_weight = sum(distance_factors)
            weighted_avg = Vector((0, 0, 0))
            for i, n in enumerate(neighbors):
                weighted_avg += n * distance_factors[i]
            weighted_avg /= total_weight

            # Blend toward the weighted average
            v.co = v.co.lerp(weighted_avg, smoothing_factor)

    bmesh.update_edit_mesh(mesh)


def register():
    bpy.utils.register_class(UVTileProperties)
    bpy.utils.register_class(SmoothVoxelTerrainPanel)
    bpy.utils.register_class(SmoothVoxelTerrainOperator)
    bpy.utils.register_class(SelectUVTileOperator)
    bpy.utils.register_class(SeparateMeshByUVTilesOperator)
    bpy.utils.register_class(QuickExportFBXOperator)
    bpy.utils.register_class(AutoSculptTerrainOperator)
    bpy.types.VIEW3D_MT_edit_mesh.append(menu_func)
    
    
    
    
    bpy.types.Scene.uv_tile_props = PointerProperty(type=UVTileProperties)
    bpy.types.Scene.smoothing_factor = FloatProperty(
        name="Smoothing Factor", 
        description="Factor to determine the amount of smoothing (0 to 1)", 
        default=0.5, 
        min=0, 
        max=1
    )

def unregister():
    bpy.utils.unregister_class(UVTileProperties)
    bpy.utils.unregister_class(SmoothVoxelTerrainPanel)
    bpy.utils.unregister_class(SmoothVoxelTerrainOperator)
    bpy.utils.unregister_class(SelectUVTileOperator)
    bpy.utils.unregister_class(SeparateMeshByUVTilesOperator)
    bpy.utils.unregister_class(QuickExportFBXOperator)
    bpy.utils.unregister_class(AutoSculptTerrainOperator)
    bpy.types.VIEW3D_MT_edit_mesh.remove(menu_func)
    
    del bpy.types.Scene.uv_tile_props
    del bpy.types.Scene.smoothing_factor

if __name__ == "__main__":
    register()
