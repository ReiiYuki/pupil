'''
(*)~----------------------------------------------------------------------------------
 Pupil - eye tracking platform
 Copyright (C) 2012-2013  Moritz Kassner & William Patera

 Distributed under the terms of the CC BY-NC-SA License.
 License details are in the file license.txt, distributed as part of this software.
----------------------------------------------------------------------------------~(*)
'''

# make shared modules available across pupil_src
if __name__ == '__main__':
    from sys import path as syspath
    from os import path as ospath
    loc = ospath.abspath(__file__).rsplit('pupil_src', 1)
    syspath.append(ospath.join(loc[0], 'pupil_src', 'shared_modules'))
    del syspath, ospath

import os, sys
from time import time
from ctypes import  c_int,c_bool,c_float,create_string_buffer
import numpy as np
import cv2
from glob import glob
from glfw import *
import atb
from methods import normalize, denormalize, chessboard, circle_grid, gen_pattern_grid, calibrate_camera,Temp
from uvc_capture import autoCreateCapture
from gl_utils import adjust_gl_view, draw_gl_texture, clear_gl_screen,draw_gl_point,draw_gl_point_norm,draw_gl_polyline_norm
from calibrate import *
import reference_detectors
import recorder
from show_calibration import Show_Calibration

def world_profiled(g_pool):
    import cProfile
    from world import world
    cProfile.runctx("world(g_pool,)",{"g_pool":g_pool},locals(),"world.pstats")

def world(g_pool):
    """world
    """

    # Callback functions
    def on_resize(w, h):
        atb.TwWindowSize(w, h);
        adjust_gl_view(w,h)

    def on_key(key, pressed):
        if not atb.TwEventKeyboardGLFW(key,pressed):
            if pressed:
                if key == GLFW_KEY_ESC:
                    on_close()

    def on_char(char, pressed):
        if not atb.TwEventCharGLFW(char,pressed):
            pass

    def on_button(button, pressed):
        if not atb.TwEventMouseButtonGLFW(button,pressed):
            if pressed:
                pos = glfwGetMousePos()
                pos = normalize(pos,glfwGetWindowSize())
                pos = denormalize(pos,(img.shape[1],img.shape[0]) ) # Position in img pixels
                for p in g.plugins:
                    p.on_click(pos)

    def on_pos(x, y):
        if atb.TwMouseMotion(x,y):
            pass

    def on_scroll(pos):
        if not atb.TwMouseWheel(pos):
            pass

    def on_close():
        g_pool.quit.value = True
        print "WORLD Process closing from window"


    # gaze object
    gaze = Temp()
    gaze.map_coords = (0., 0.)
    gaze.image_coords = (0., 0.)

    # Initialize capture, check if it works
    cap = autoCreateCapture(g_pool.world_src, g_pool.world_size)
    if cap is None:
        print "WORLD: Error could not create Capture"
        return
    s, img = cap.read()
    if not s:
        print "WORLD: Error could not get image"
        return
    height,width = img.shape[:2]


    # helpers called by the main atb bar
    def update_fps():
        old_time, bar.timestamp = bar.timestamp, time()
        dt = bar.timestamp - old_time
        if dt:
            bar.fps.value += .05 * (1 / dt - bar.fps.value)

    def set_window_size(mode,data):
        height,width = img.shape[:2]
        ratio = (1,.75,.5,.25)[mode]
        w,h = int(width*ratio),int(height*ratio)
        glfwSetWindowSize(w,h)
        data.value=mode # update the bar.value

    def get_from_data(data):
        """
        helper for atb getter and setter use
        """
        return data.value

    def open_calibration(selection,data):
        # prepare destruction of old ref_detector.
        if g.current_ref_detector:
            g.current_ref_detector.alive = False

        # remove old ref detector from list of plugins
        g.plugins = [p for p in g.plugins if p.alive]

        print "selected: ",reference_detectors.name_by_index[selection]
        g.current_ref_detector = reference_detectors.detector_by_index[selection](global_calibrate=g_pool.calibrate,
                                                                    shared_pos=g_pool.ref,
                                                                    screen_marker_pos = g_pool.marker,
                                                                    screen_marker_state = g_pool.marker_state,
                                                                    atb_pos=bar.next_atb_pos)

        g.plugins.append(g.current_ref_detector)
        # save the value for atb bar
        data.value=selection

    def toggle_record_video():
        if any([True for p in g.plugins if isinstance(p,recorder.Recorder)]):
            # stop and schedule for deletion
            [p.stop_and_destruct() for p in g.plugins if isinstance(p,recorder.Recorder)]
        else:
            # set up folder within recordings named by user input in atb
            if not bar.rec_name.value:
                bar.rec_name.value = recorder.get_auto_name()
            recorder_instance = recorder.Recorder(bar.rec_name.value, bar.fps.value, img.shape, g_pool.pos_record,
                                g_pool.frame_count_record, g_pool.eye_tx)
            g.plugins.append(recorder_instance)

    def toggle_show_calib_result():
        if any([True for p in g.plugins if isinstance(p,Show_Calibration)]):
            for p in g.plugins:
                if isinstance(p,Show_Calibration):
                    p.alive = False
            print "calibration results closed"
        else:
            calib = Show_Calibration(img.shape)
            g.plugins.append(calib)

    def show_calib_result():
        # kill old if any
        if any([True for p in g.plugins if isinstance(p,Show_Calibration)]):
            for p in g.plugins:
                if isinstance(p,Show_Calibration):
                    p.alive = False
            g.plugins = [p for p in g.plugins if p.alive]
        # make new
        calib = Show_Calibration(img.shape)
        g.plugins.append(calib)

    def hide_calib_result():
        if any([True for p in g.plugins if isinstance(p,Show_Calibration)]):
            for p in g.plugins:
                if isinstance(p,Show_Calibration):
                    p.alive = False

    # Initialize ant tweak bar - inherits from atb.Bar
    atb.init()
    bar = atb.Bar(name = "World", label="Controls",
            help="Scene controls", color=(50, 50, 50), alpha=100,valueswidth=150,
            text='light', position=(10, 10),refresh=.3, size=(300, 200))
    bar.next_atb_pos = (10,220)
    bar.fps = c_float(0.0)
    bar.timestamp = time()
    bar.calibration_type = c_int(0)
    bar.show_calib_result = c_bool(0)
    bar.record_video = c_bool(0)
    bar.record_running = c_bool(0)
    bar.play = g_pool.play
    bar.window_size = c_int(0)
    window_size_enum = atb.enum("Display Size",{"Full":0, "Medium":1,"Half":2,"Mini":3})

    bar.calibrate_type_enum = atb.enum("Calibration Method",reference_detectors.index_by_name)
    bar.rec_name = create_string_buffer(512)
    bar.rec_name.value = recorder.get_auto_name()
    # play and record can be tied together via pointers to the objects
    # bar.play = bar.record_video
    bar.add_var("fps", bar.fps, step=1., readonly=True)
    bar.add_var("display size", vtype=window_size_enum,setter=set_window_size,getter=get_from_data,data=bar.window_size)
    bar.add_var("calibration method",setter=open_calibration,getter=get_from_data,data=bar.calibration_type, vtype=bar.calibrate_type_enum,group="Calibration", help="Please choose your desired calibration method.")
    bar.add_button("show calibration result",toggle_show_calib_result, group="Calibration", help="Click to show calibration result.")
    bar.add_var("session name",bar.rec_name, group="Recording", help="creates folder Data_Name_XXX, where xxx is an increasing number")
    bar.add_button("start recording", toggle_record_video, key="r", group="Recording", help="Start/Stop Recording")
    bar.add_separator("Sep1")
    bar.add_var("play video", bar.play, help="play a video in the Player window")
    bar.add_var("exit", g_pool.quit)


    # add uvc camera controls to a seperate ATB bar
    if cap.controls is not None:
        c_bar = atb.Bar(name="Camera_Controls", label=cap.name,
            help="UVC Camera Controls", color=(50,50,50), alpha=100,
            text='light',position=(320, 10),refresh=2., size=(200, 200))

        sorted_controls = [c for c in cap.controls.itervalues()]
        sorted_controls.sort(key=lambda c: c.order)

        for control in sorted_controls:
            name = control.atb_name
            if control.type=="bool":
                c_bar.add_var(name,vtype=atb.TW_TYPE_BOOL8,getter=control.get_val,setter=control.set_val)
            elif control.type=='int':
                c_bar.add_var(name,vtype=atb.TW_TYPE_INT32,getter=control.get_val,setter=control.set_val)
                c_bar.define(definition='min='+str(control.min),   varname=name)
                c_bar.define(definition='max='+str(control.max),   varname=name)
                c_bar.define(definition='step='+str(control.step), varname=name)
            elif control.type=="menu":
                if control.menu is None:
                    vtype = None
                else:
                    vtype= atb.enum(name,control.menu)
                c_bar.add_var(name,vtype=vtype,getter=control.get_val,setter=control.set_val)
                if control.menu is None:
                    c_bar.define(definition='min='+str(control.min),   varname=name)
                    c_bar.define(definition='max='+str(control.max),   varname=name)
                    c_bar.define(definition='step='+str(control.step), varname=name)
            else:
                pass
            if control.flags == "inactive":
                pass
                # c_bar.define(definition='readonly=1',varname=control.name)

        c_bar.add_button("refresh",cap.update_from_device)
        c_bar.add_button("load defaults",cap.load_defaults)

    else:
        c_bar = None


    # create container for globally scoped vars (within world)
    g = Temp()
    g.plugins = []
    g.current_ref_detector = None
    open_calibration(0,bar.calibration_type)

    # Initialize glfw
    glfwInit()
    height,width = img.shape[:2]
    glfwOpenWindow(width, height, 0, 0, 0, 8, 0, 0, GLFW_WINDOW)
    glfwSetWindowTitle("World")
    glfwSetWindowPos(0,0)

    # Register callbacks
    glfwSetWindowSizeCallback(on_resize)
    glfwSetWindowCloseCallback(on_close)
    glfwSetKeyCallback(on_key)
    glfwSetCharCallback(on_char)
    glfwSetMouseButtonCallback(on_button)
    glfwSetMousePosCallback(on_pos)
    glfwSetMouseWheelCallback(on_scroll)

    # gl_state settings
    import OpenGL.GL as gl
    gl.glEnable(gl.GL_POINT_SMOOTH)
    gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
    gl.glEnable(gl.GL_BLEND)
    del gl

    # Event loop
    while glfwGetWindowParam(GLFW_OPENED) and not g_pool.quit.value:
        update_fps()

        # Get input characters entered in player
        if g_pool.player_input.value:
            player_input = g_pool.player_input.value
            g_pool.player_input.value = 0
            on_char(player_input,True)

        # Get an image from the grabber
        s, img = cap.read()

        for p in g.plugins:
            p.update(img)

        g.plugins = [p for p in g.plugins if p.alive]

        g_pool.player_refresh.set()

        # render the screen
        clear_gl_screen()
        draw_gl_texture(img)

        # render visual feedback from loaded plugins
        for p in g.plugins:
            p.gl_display()


        # update gaze point from shared variable pool and draw on screen. If both coords are 0: no pupil pos was detected.
        if not g_pool.gaze[:] == [0.,0.]:
            draw_gl_point_norm(g_pool.gaze[:],color=(1.,0.,0.,0.5))

        atb.draw()
        glfwSwapBuffers()

    # end while running and clean-up
    print "WORLD Process closed"
    glfwCloseWindow()
    glfwTerminate()

