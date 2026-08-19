import React, { useEffect, useRef, useCallback, useMemo } from "react";
import { Excalidraw, restoreElements } from "@excalidraw/excalidraw";

export function ExcalidrawCanvas({
  initialData,
  sceneData,
  onNodeSelect,
  onReady,
  isEditMode = false,
}) {
  const excalidrawAPIRef = useRef(null);
  const containerRef = useRef(null);

  const handleAPISet = useCallback((api) => {
    excalidrawAPIRef.current = api;
    if (onReady) onReady(api);
  }, [onReady]);

  // Update scene when epoch / system scene data changes
  useEffect(() => {
    if (excalidrawAPIRef.current && sceneData && sceneData.elements) {
      const restored = restoreElements(
        sceneData.elements.map((el) => ({ ...el, locked: !isEditMode })),
        null,
        {
          refreshDimensions: false,
          repairBindings: true,
        }
      );
      excalidrawAPIRef.current.updateScene({
        elements: restored,
        appState: {
          ...(sceneData.appState || {}),
          viewModeEnabled: !isEditMode,
        },
      });
    }
  }, [sceneData, isEditMode]);

  // Update elements lock state and viewMode when edit mode toggles
  useEffect(() => {
    if (!excalidrawAPIRef.current) return;
    const currentElements = excalidrawAPIRef.current.getSceneElements();
    if (currentElements && currentElements.length > 0) {
      const updated = currentElements.map((el) => ({
        ...el,
        locked: !isEditMode,
      }));
      excalidrawAPIRef.current.updateScene({
        elements: updated,
        appState: { viewModeEnabled: !isEditMode },
      });
    }
  }, [isEditMode]);

  const restoredInitialData = useMemo(() => {
    if (!initialData || !initialData.elements) return initialData;
    const elementsWithLock = initialData.elements.map((el) => ({
      ...el,
      locked: !isEditMode,
    }));
    return {
      ...initialData,
      appState: {
        ...(initialData.appState || {}),
        viewModeEnabled: !isEditMode,
      },
      elements: restoreElements(elementsWithLock, null, {
        refreshDimensions: false,
        repairBindings: true,
      }),
    };
  }, [initialData, isEditMode]);

  // Handle double-click specifically on a component element via hit-testing
  const handleContainerDoubleClick = useCallback((e) => {
    if (!excalidrawAPIRef.current) return;
    const appState = excalidrawAPIRef.current.getAppState();
    const elements = excalidrawAPIRef.current.getSceneElements();

    // 1. Check if an element was selected (e.g. in Edit mode)
    const selectedIds = Object.keys(appState.selectedElementIds || {});
    if (selectedIds.length > 0) {
      for (const id of selectedIds) {
        const el = elements.find((item) => item.id === id);
        if (el && el.customData && el.customData.nodeId) {
          if (onNodeSelect) {
            onNodeSelect(el.customData.nodeId, el);
          }
          return;
        }
      }
    }

    // 2. Perform direct spatial geometric hit-testing from click coordinates
    if (containerRef.current && e) {
      const rect = containerRef.current.getBoundingClientRect();
      const zoom = appState.zoom ? appState.zoom.value : 1;
      const sceneX = (e.clientX - rect.left) / zoom - appState.scrollX;
      const sceneY = (e.clientY - rect.top) / zoom - appState.scrollY;

      // Find top-most node card that contains (sceneX, sceneY)
      for (const el of elements) {
        if (el.customData && el.customData.nodeId && el.type === "rectangle") {
          if (
            sceneX >= el.x &&
            sceneX <= el.x + el.width &&
            sceneY >= el.y &&
            sceneY <= el.y + el.height
          ) {
            if (onNodeSelect) {
              onNodeSelect(el.customData.nodeId, el);
            }
            return;
          }
        }
      }

      // Check text or other child elements with nodeId
      for (const el of elements) {
        if (el.customData && el.customData.nodeId) {
          if (
            sceneX >= el.x &&
            sceneX <= el.x + el.width &&
            sceneY >= el.y &&
            sceneY <= el.y + el.height
          ) {
            if (onNodeSelect) {
              onNodeSelect(el.customData.nodeId, el);
            }
            return;
          }
        }
      }
    }
  }, [onNodeSelect]);

  return (
    <main
      ref={containerRef}
      className="flex-1 h-full relative bg-[#ede6d1]"
      onDoubleClickCapture={handleContainerDoubleClick}
    >
      <Excalidraw
        excalidrawAPI={handleAPISet}
        initialData={restoredInitialData}
        gridModeEnabled={true}
        zenModeEnabled={false}
        viewModeEnabled={!isEditMode}
        UIOptions={{
          canvasActions: {
            changeViewBackgroundColor: true,
            clearCanvas: false,
            loadScene: false,
            saveToActiveFile: false,
            toggleTheme: false,
            saveAsImage: true,
          },
        }}
      />
    </main>
  );
}
