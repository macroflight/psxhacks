"""Show events on all connected USB joystick devices."""
import pygame
import sys
import time

joysticks = {}
pygame.init()
for i in range(pygame.joystick.get_count()):
    joystick_name = pygame.joystick.Joystick(i).get_name()
    joy = pygame.joystick.Joystick(i)
    joy.init()
    joysticks[joy.get_instance_id()] = joy

    while True:
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                joystick = joysticks[event.instance_id]
                print(f"Button {event.button} DOWN event received for: {joystick.get_name()}")
            elif event.type == pygame.JOYBUTTONUP:
                joystick = joysticks[event.instance_id]
                print(f"Button {event.button} UP event received for: {joystick.get_name()}")
            elif event.type == pygame.JOYAXISMOTION:
                joystick = joysticks[event.instance_id]
                print(f"Axis movement on \"{joystick.get_name()}\" axis {event.axis} to position {joystick.get_axis(event.axis)}")
            elif event.type == pygame.JOYHATMOTION:
                joystick = joysticks[event.instance_id]
                print(f"Hat movement on \"{joystick.get_name()}\" hat {event.hat} to position {joystick.get_hat(event.hat)}")
